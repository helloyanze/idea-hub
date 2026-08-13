"""常规执行器：幂等/生成/质检重试/规则层/版本保留/失败计数。"""
import httpx
import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

# 测试全程 mock LLM，不真实调 API；但 call_llm 需先取到 API key 才会走到 httpx.post，
# 故提供一个假 key（setdefault 不覆盖真实环境变量）。
os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")

from idea_hub import db, executor, models, prompts

def _setup(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)  # connect 不自动建表，按仓库惯例显式初始化
    # target_id=1 外键依赖 targets 表，需先建行（首个插入 id=1，且 is_active=1 供 get_active_target）
    conn.execute("INSERT INTO targets (name, description, score_dimensions, is_active) VALUES (?,?,?,1)",
                 ("测试目标", "测试", "{}"))
    conn.commit()
    tid = models.create_task(conn, title="测试任务", idea_summary="摘要",
                             target_id=1, feasibility_score=9,
                             score_breakdown="{}", content_type="long")
    # 调度器 try_start 后任务才是 in_progress，executor 只处理 in_progress
    models.move_task(conn, tid, "in_progress")
    d = Path(tmp_path) / "outputs" / "tasks" / str(tid)
    d.mkdir(parents=True)
    (d / "idea.md").write_text("# 构思\n深度内容", encoding="utf-8")
    return conn, tid

def _fake_call_llm(returns):
    """返回 (content, tokens) 的迭代器模拟。"""
    it = iter(returns)
    def f(prompt, *, timeout=120):
        return next(it)
    return f

def test_idempotent_skip_when_output_exists(tmp_path):
    conn, tid = _setup(tmp_path)
    d = Path(tmp_path) / "outputs" / "tasks" / str(tid)
    (d / "output.md").write_text("已有产出", encoding="utf-8")
    with patch.object(executor, "call_llm", side_effect=AssertionError("不应调用 API")):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 2
    assert models.get_task(conn, tid)["status"] == "done"

def test_happy_path_done_and_versioning(tmp_path):
    conn, tid = _setup(tmp_path)
    d = Path(tmp_path) / "outputs" / "tasks" / str(tid)
    (d / "output.md").write_text("旧版", encoding="utf-8")  # 模拟打回重做有旧版
    # 打回重做会写 redo_note：有 redo_note 的旧 output 应被版本化保留并重新生成，
    # 而非走幂等完成分支（幂等分支只服务于 complete 中断后的 crash-recovery 重跑）
    models.update_task(conn, tid, redo_note="重新写一版")
    payload = json.dumps({"title": "新标题", "content": "正文内容" * 100,
                          "word_count": 300}, ensure_ascii=False)
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    calls = [("llm_gen", 1000), ("qa", 300)]
    def fake(prompt, *, timeout=120):
        kind = calls.pop(0)
        return (payload if kind[0] == "llm_gen" else qa_ok), kind[1]
    with patch.object(executor, "call_llm", side_effect=fake):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    task = models.get_task(conn, tid)
    assert task["status"] == "done"
    assert task["token_used"] == 1300
    assert task["fail_count"] == 0
    assert (d / "output.md").exists()
    assert (d / "output_v1.md").read_text(encoding="utf-8") == "旧版"

def test_qa_fail_then_regenerate_then_pass(tmp_path):
    conn, tid = _setup(tmp_path)
    payload_ok = json.dumps({"title": "t", "content": "好内容" * 100, "word_count": 300})
    payload_v2 = json.dumps({"title": "t2", "content": "修正后内容" * 100, "word_count": 300})
    qa_fail = json.dumps({"pass": False,
                          "issues": [{"type": "style", "quote": "值得注意的是",
                                      "problem": "模板词", "fix": "删除"}],
                          "suggestions": "口语化"})
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload_ok, 1000), (qa_fail, 300), (payload_v2, 1000), (qa_ok, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    task = models.get_task(conn, tid)
    assert task["status"] == "done"
    assert task["token_used"] == 2600  # 两次生成 + 两次质检

def test_qa_fail_twice_fails_task(tmp_path):
    conn, tid = _setup(tmp_path)
    payload = json.dumps({"title": "t", "content": "差内容" * 100, "word_count": 300})
    qa_bad = json.dumps({"pass": False,
                         "issues": [{"type": "structure", "quote": "开头",
                                     "problem": "无钩子", "fix": "加钩子"}],
                         "suggestions": "重写"})
    seq = [(payload, 1000), (qa_bad, 300), (payload, 1000), (qa_bad, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 1
    task = models.get_task(conn, tid)
    assert task["status"] == "waiting"  # fail 退回
    assert task["fail_count"] == 1
    assert task["last_fail_reason"] and "质检" in task["last_fail_reason"]

def test_ai_taste_rule_triggers_regenerate(tmp_path):
    conn, tid = _setup(tmp_path)
    payload_bad = json.dumps({"title": "t", "content": "首先，值得注意的是，本内容很模板。",
                              "word_count": 100})
    payload_ok = json.dumps({"title": "t", "content": "自然口语的好内容" * 100, "word_count": 300})
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload_bad, 1000), (payload_ok, 1000), (qa_ok, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0  # 规则层命中 → 重生成 → 通过
    assert models.get_task(conn, tid)["status"] == "done"

def test_call_llm_timeout_raises():
    # httpx 真实超时抛 httpx.ReadTimeout（TimeoutException 子类，非内置 TimeoutError），
    # 契约要求 call_llm 将其转换为内置 TimeoutError
    with patch("httpx.post", side_effect=httpx.ReadTimeout("timeout")):
        try:
            executor.call_llm("prompt", timeout=1)
            assert False, "应抛异常"
        except TimeoutError:
            pass

def test_empty_output_not_idempotent(tmp_path):
    """output.md 存在但 size=0（空文件）→ 不走幂等分支，走正常生成流程。"""
    conn, tid = _setup(tmp_path)
    d = Path(tmp_path) / "outputs" / "tasks" / str(tid)
    (d / "output.md").write_text("", encoding="utf-8")  # 空文件 st_size=0
    payload = json.dumps({"title": "新标题", "content": "正文内容" * 100,
                          "word_count": 300}, ensure_ascii=False)
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload, 1000), (qa_ok, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    assert models.get_task(conn, tid)["status"] == "done"
    assert (d / "output.md").read_text(encoding="utf-8") == "正文内容" * 100

def test_qa_parse_failure_fails_task(tmp_path):
    """质检输出非 JSON → 解析失败视为不通过，重试仍失败 → 任务退回 waiting。"""
    conn, tid = _setup(tmp_path)
    payload = json.dumps({"title": "t", "content": "差内容" * 100, "word_count": 300})
    seq = [(payload, 1000), ("这不是 JSON 的质检输出", 300),
           (payload, 1000), ("这不是 JSON 的质检输出", 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 1
    task = models.get_task(conn, tid)
    assert task["status"] == "waiting"
    assert task["fail_count"] == 1
    assert "质检" in task["last_fail_reason"]

def test_done_sends_notification(tmp_path):
    """通知闭环：_complete_task 成功后必须发 type=done 通知（含任务标题与摘要）。"""
    conn, tid = _setup(tmp_path)
    payload = json.dumps({"title": "新标题", "content": "正文内容" * 100,
                          "word_count": 300}, ensure_ascii=False)
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload, 1000), (qa_ok, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)), \
         patch("idea_hub.notify.send") as ns:
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    done = [c for c in ns.call_args_list if c.kwargs.get("type") == "done"]
    assert len(done) == 1
    assert done[0].kwargs["task_id"] == tid
    assert "测试任务" in done[0].kwargs["body"]      # 标题取自任务标题
    assert "摘要" in done[0].kwargs["body"] and "新标题" in done[0].kwargs["body"]


def test_fail_sends_notification(tmp_path):
    """通知闭环：_fail_task 后必须发 type=failed 通知（含原因与退回说明）。"""
    conn, tid = _setup(tmp_path)
    payload = json.dumps({"title": "t", "content": "差内容" * 100, "word_count": 300})
    qa_bad = json.dumps({"pass": False,
                         "issues": [{"type": "structure", "quote": "开头",
                                     "problem": "无钩子", "fix": "加钩子"}],
                         "suggestions": "重写"})
    seq = [(payload, 1000), (qa_bad, 300), (payload, 1000), (qa_bad, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)), \
         patch("idea_hub.notify.send") as ns:
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 1
    failed = [c for c in ns.call_args_list if c.kwargs.get("type") == "failed"]
    assert len(failed) == 1
    assert failed[0].kwargs["task_id"] == tid
    assert "质检" in failed[0].kwargs["body"] and "等待队列" in failed[0].kwargs["body"]


def test_fail_reaching_max_count_sends_paused(tmp_path):
    """失败暂停：fail_count 达 max_fail_count 阈值时额外发 type=paused 通知。"""
    conn, tid = _setup(tmp_path)
    models.set_setting(conn, "max_fail_count", "1")
    payload = json.dumps({"title": "t", "content": "差内容" * 100, "word_count": 300})
    qa_bad = json.dumps({"pass": False,
                         "issues": [{"type": "structure", "quote": "开头",
                                     "problem": "无钩子", "fix": "加钩子"}],
                         "suggestions": "重写"})
    seq = [(payload, 1000), (qa_bad, 300), (payload, 1000), (qa_bad, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)), \
         patch("idea_hub.notify.send") as ns:
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 1
    types = [c.kwargs.get("type") for c in ns.call_args_list]
    assert "failed" in types and "paused" in types
    paused = next(c for c in ns.call_args_list if c.kwargs.get("type") == "paused")
    assert "暂停" in paused.kwargs["body"]


def test_fail_then_complete_accumulates_token_used(tmp_path):
    """失败(打回 waiting)后再次执行成功的场景：token_used 应两轮累加而非被覆盖。"""
    conn, tid = _setup(tmp_path)
    payload = json.dumps({"title": "t", "content": "差内容" * 100, "word_count": 300})
    qa_bad = json.dumps({"pass": False,
                         "issues": [{"type": "structure", "quote": "开头",
                                     "problem": "无钩子", "fix": "加钩子"}],
                         "suggestions": "重写"})
    seq1 = [(payload, 1000), (qa_bad, 300), (payload, 1000), (qa_bad, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq1.pop(0)):
        assert executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path)) == 1
    assert models.get_task(conn, tid)["token_used"] == 2600
    # 打回后调度再次 start → in_progress
    models.move_task(conn, tid, "in_progress")
    payload_ok = json.dumps({"title": "t2", "content": "好内容" * 100, "word_count": 300})
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq2 = [(payload_ok, 1000), (qa_ok, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq2.pop(0)):
        assert executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path)) == 0
    task = models.get_task(conn, tid)
    assert task["status"] == "done"
    assert task["token_used"] == 3900  # 2600(失败轮) + 1300(成功轮)
