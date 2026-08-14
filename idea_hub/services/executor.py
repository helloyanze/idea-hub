"""单任务执行器：LLM 生成正式内容 → outputs v1 落盘 → 状态流转。

幂等：done 且有产物 → 跳过；done 无产物 → 补执行。
冲突：条件 UPDATE rowcount=0（任务被外部移走/占用）→ 产物仍写 + conflict 标记 + warn 通知，
不修改任务状态。失败：in_progress → waiting + fail_count+1 + last_fail_reason。
"""
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime

from ..services import llm
from ..services.notify import emit

logger = logging.getLogger(__name__)


@dataclass
class ExecuteResult:
    ok: bool
    token_used: int = 0
    error: str | None = None
    conflict: bool = False
    saved_output: bool = False


def build_execute_prompt(task: dict) -> list[dict]:
    """根据任务信息构建正式内容生成提示。"""
    system = '你是资深中文内容创作者。根据任务信息创作一篇完整的正式内容，使用 Markdown 格式，结构清晰、有事实依据、表达自然，避免模板化套话（如"首先/其次/最后/总的来说/值得注意的是"）。直接输出正文 Markdown，不要输出任何额外说明。'
    user = (
        f"任务标题：{task['title']}\n"
        f"目标描述：{task.get('target_desc') or ''}\n"
        f"构思摘要：{task.get('idea_summary') or ''}\n"
        f"内容类型：{task.get('content_type') or 'article'}\n"
        "请直接输出完整的 Markdown 正文内容。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def execute_one(conn, task_id, api_key, heartbeat=None, base_path=None) -> ExecuteResult:
    """执行单个任务并将正式内容写入 outputs v1。"""
    if base_path is None:
        from ..config import load as load_config

        base_path = load_config().base_path

    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        return ExecuteResult(ok=False, error=f"Task not found: {task_id}")

    has_output = conn.execute(
        "SELECT 1 FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchone() is not None
    if row["status"] == "done" and has_output:
        return ExecuteResult(ok=True)

    cursor = conn.execute(
        "UPDATE tasks SET status='in_progress', updated_at=datetime('now') "
        "WHERE id=? AND (status IN ('todo','waiting') "
        "OR (status='done' AND NOT EXISTS (SELECT 1 FROM outputs WHERE task_id=?)))",
        (task_id, task_id),
    )
    conn.commit()
    claimed = cursor.rowcount > 0
    conflict = not claimed

    try:
        messages = build_execute_prompt(dict(row))
        usage = {}
        content = llm.chat_text(
            messages, api_key, heartbeat=heartbeat, token_usage=usage
        )
        token_used = int(usage.get("total") or 0)
        if not content or not content.strip():
            raise ValueError("LLM 输出为空")
    except Exception as exc:
        if claimed:
            conn.execute(
                "UPDATE tasks SET status='waiting', fail_count=fail_count+1, "
                "last_fail_reason=?, updated_at=datetime('now') "
                "WHERE id=? AND status='in_progress'",
                (str(exc)[:500], task_id),
            )
            conn.commit()
        return ExecuteResult(ok=False, error=str(exc), conflict=conflict)

    summary = " ".join(content.split())[:60]
    file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        existing = conn.execute(
            "SELECT id FROM outputs WHERE task_id=? AND version=1", (task_id,)
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE outputs SET content=?, filename='output.md', "
                "updated_at=datetime('now') WHERE id=?",
                (content, existing["id"]),
            )
            output_id = existing["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO outputs (task_id, version, filename, content) "
                "VALUES (?, 1, 'output.md', ?)",
                (task_id, content),
            )
            output_id = cursor.lastrowid
        out_dir = os.path.join(base_path, "outputs", "tasks", str(task_id))
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "output.md")
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        mtime = datetime.fromtimestamp(os.path.getmtime(out_path)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn.execute(
            "UPDATE outputs SET file_mtime=?, file_hash=? WHERE id=?",
            (mtime, file_hash, output_id),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        if claimed:
            conn.execute(
                "UPDATE tasks SET status='waiting', fail_count=fail_count+1, "
                "last_fail_reason=?, updated_at=datetime('now') "
                "WHERE id=? AND status='in_progress'",
                (str(exc)[:500], task_id),
            )
            conn.commit()
        return ExecuteResult(
            ok=False,
            token_used=token_used,
            error=str(exc),
            conflict=conflict,
        )

    if claimed:
        cur = conn.execute(
            "UPDATE tasks SET status='done', completed_at=datetime('now'), "
            "ai_summary=?, token_used=token_used+?, updated_at=datetime('now') "
            "WHERE id=? AND status='in_progress'",
            (summary, token_used, task_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            conflict = True

    if conflict:
        emit(
            conn,
            "job_failed",
            "执行状态冲突",
            f"任务 {task_id} 状态已被外部修改，产物已写入但任务状态未变更",
            "warn",
            entity_type="task",
            entity_id=task_id,
        )

    return ExecuteResult(
        ok=True,
        token_used=token_used,
        conflict=conflict,
        saved_output=True,
    )
