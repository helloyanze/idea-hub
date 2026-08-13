"""常规任务执行器（独立子进程运行）：幂等 → 生成 → 质检重试 → 落盘 → complete。"""
import json
import pathlib
import re
import sys

import httpx

from idea_hub import db, models, prompts
from idea_hub.scorer import LLM_URL, LLM_MODEL, llm_key

LLM_TIMEOUT = 120
QA_TIMEOUT = 60
MAX_GENERATE_ATTEMPTS = 2  # 首次 + 质检重试 1 次


def call_llm(prompt, *, timeout=LLM_TIMEOUT):
    """调 DeepSeek API，返回 (文本, 本次 token 数)。超时抛 TimeoutError。"""
    key = llm_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    try:
        resp = httpx.post(LLM_URL, timeout=timeout,
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": LLM_MODEL, "messages": [
                              {"role": "user", "content": prompt}],
                              "temperature": 0.7,
                              "response_format": {"type": "json_object"}})
    except httpx.TimeoutException as exc:
        # 契约：超时统一抛内置 TimeoutError（httpx 的 ReadTimeout/ConnectTimeout 等
        # 均非 TimeoutError 子类，需在此转换）
        raise TimeoutError("LLM 调用超时") from exc
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    tokens = data.get("usage", {}).get("total_tokens", 0)
    return content, tokens


def run_qa(content, content_type, timeout=QA_TIMEOUT):
    """质检。返回 (qa_dict, tokens)。解析失败视为不通过。"""
    raw, tokens = call_llm(prompts.build_qa_prompt(content, content_type), timeout=timeout)
    qa = prompts.parse_llm_json(raw)
    if not qa:
        qa = {"pass": False, "issues": [{"type": "structure", "quote": "",
                                         "problem": "质检输出解析失败",
                                         "fix": "重新生成"}], "suggestions": ""}
    return qa, tokens


def _version_output(base_path, task_id):
    """output.md 已存在则按最大版本号+1 改名保留旧版；返回新 output.md 路径。"""
    d = pathlib.Path(base_path) / "outputs" / "tasks" / str(task_id)
    d.mkdir(parents=True, exist_ok=True)
    target = d / "output.md"
    if target.exists():
        nums = []
        for p in d.glob("output_v*.md"):
            m = re.match(r"output_v(\d+)\.md", p.name)
            if m:
                nums.append(int(m.group(1)))
        n = (max(nums) + 1) if nums else 1
        target.rename(d / f"output_v{n}.md")
    return target


def _complete_task(conn, task_id, summary, output_path, token_used):
    rel = str(pathlib.Path("outputs") / "tasks" / str(task_id) / "output.md").replace("\\", "/")
    task = models.get_task(conn, task_id)
    models.update_task(conn, task_id, ai_summary=summary, output_path=rel,
                       token_used=(task["token_used"] or 0) + token_used,
                       fail_count=0, last_fail_reason=None)
    models.move_task(conn, task_id, "done")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (task_id,))
    conn.commit()


def _fail_task(conn, task_id, reason, token_used):
    task = models.get_task(conn, task_id)
    models.update_task(conn, task_id,
                       fail_count=(task["fail_count"] or 0) + 1,
                       last_fail_reason=reason,
                       token_used=(task["token_used"] or 0) + token_used)
    models.move_task(conn, task_id, "waiting")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (task_id,))
    conn.commit()


def _summary_from(payload: dict, content: str) -> str:
    """完成摘要：优先用 LLM 输出的 title，否则截取正文首 60 字。"""
    title = (payload.get("title") or "").strip()
    if title:
        return title
    plain = re.sub(r"\s+", " ", content).strip()
    return plain[:60]


def execute_task(db_path, task_id, base_path):
    """执行单个常规任务。返回 0=done, 1=failed, 2=幂等直接完成。"""
    conn = db.connect(db_path)
    try:
        task = models.get_task(conn, task_id)
        if task is None:
            print(f"error: task {task_id} not found", file=sys.stderr)
            conn.close()
            return 1
        if task["status"] != "in_progress":
            print(f"skip: task {task_id} status={task['status']}", file=sys.stderr)
            conn.close()
            return 1
        d = pathlib.Path(base_path) / "outputs" / "tasks" / str(task_id)
        if not task.get("redo_note") and (d / "output.md").exists() and (d / "output.md").stat().st_size > 0:
            # 幂等：产出已存在且非打回重做（如 complete 网络中断后重跑）→ 直接完成
            # token_used 传 0：_complete_task 会累加现值，保留此前累计 token
            _complete_task(conn, task_id, task.get("ai_summary") or "已完成", d / "output.md", 0)
            conn.close()
            return 2

        target = models.get_active_target(conn) or {"name": "默认", "description": ""}
        hot_titles = [r["title"] for r in conn.execute(
            "SELECT h.title FROM hot_items h JOIN task_links tl ON tl.hot_item_id=h.id "
            "WHERE tl.task_id=?", (task_id,)).fetchall()]
        base_prompt = prompts.build_generation_prompt(task, target, hot_titles,
                                                      task.get("redo_note"))
        total_tokens = 0
        payload = {}
        content = ""
        for attempt in range(MAX_GENERATE_ATTEMPTS):
            raw, tokens = call_llm(base_prompt)
            total_tokens += tokens
            payload = prompts.parse_llm_json(raw)
            content = (payload.get("content") or "").strip()
            if not content:
                # 输出非法：视为一次失败，直接进入重试分支
                qa = {"pass": False,
                      "issues": [{"type": "structure", "quote": "", "problem": "LLM 输出为空或非法",
                                  "fix": "重新生成"}], "suggestions": ""}
            else:
                # AI 味规则层（0 token）：命中模板词 → 直接重生成
                blacklist = (models.get_setting(conn, "ai_taste_blacklist",
                                                "首先,其次,最后,总的来说,值得注意的是,综上所述,众所周知,不言而喻,赋能,抓手,闭环")
                             or "").split(",")
                hits = prompts.check_ai_taste(content, blacklist)
                if hits:
                    qa = {"pass": False,
                          "issues": [{"type": "style", "quote": hits[0],
                                      "problem": f"命中模板词：{'、'.join(hits)}",
                                      "fix": "删除或改写为自然表达"}],
                          "suggestions": "去除模板化表达"}
                else:
                    qa, qa_tokens = run_qa(content, task.get("content_type") or "long")
                    total_tokens += qa_tokens
            if qa.get("pass"):
                break
            if attempt == MAX_GENERATE_ATTEMPTS - 1:
                reason = "质检未通过: " + json.dumps(qa, ensure_ascii=False)[:200]
                _fail_task(conn, task_id, reason, total_tokens)
                conn.close()
                return 1
            base_prompt = prompts.build_regenerate_prompt(base_prompt, qa)

        out = _version_output(base_path, task_id)
        out.write_text(content, encoding="utf-8")
        summary = _summary_from(payload, content)
        _complete_task(conn, task_id, summary, out, total_tokens)
        # 预算完成时复核（通知由调度/前端轮询兜底，此处只记录）
        conn.close()
        return 0
    except Exception as exc:
        try:
            _fail_task(conn, task_id, f"执行异常: {exc}", 0)
        except Exception:
            pass
        conn.close()
        return 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Idea Hub 常规任务执行器（供调度器 spawn 子进程）")
    ap.add_argument("--db", required=True, help="SQLite 数据库路径")
    ap.add_argument("--task-id", required=True, type=int, help="任务 ID")
    ap.add_argument("--base", required=True, help="项目根目录（outputs 所在）")
    args = ap.parse_args()
    sys.exit(execute_task(args.db, args.task_id, args.base))
