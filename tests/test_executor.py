"""Task S6.1: 执行器 services/executor.py — execute_one 单任务执行。

Covers: 成功链路（outputs v1 落盘 + done + token 累加）、LLM 失败回退 waiting + fail_count、
幂等跳过（done 有产物）、done 无产物补执行、状态冲突两场景（claim 冲突 / 完成期冲突产物仍写）、
任务不存在、冲突路径 LLM 失败不碰状态、redo 累加不清零。
"""
import hashlib

import pytest

from idea_hub.services import executor, llm

CONTENT = "# 智能硬件周报\n\n本周热点：AI 眼镜出货量增长，端侧模型落地加速。"


def seed_task(conn, *, status="todo", token_used=0, fail_count=0,
              title="任务A", idea_summary="摘要A", content_type="article",
              target_desc="热点A"):
    cur = conn.execute(
        "INSERT INTO tasks (title, idea_summary, content_type, status, "
        "feasibility_score, score_breakdown, target_desc, token_used, fail_count) "
        "VALUES (?, ?, ?, ?, 9, '{}', ?, ?, ?)",
        (title, idea_summary, content_type, status, target_desc,
         token_used, fail_count),
    )
    conn.commit()
    return cur.lastrowid


def seed_output(conn, task_id, content="旧内容"):
    conn.execute(
        "INSERT INTO outputs (task_id, version, filename, content) "
        "VALUES (?, 1, 'output.md', ?)",
        (task_id, content),
    )
    conn.commit()


def make_chat_text(content=CONTENT, token=120, side_effect=None, error=None):
    """返回注入 token_usage 的 chat_text mock；error 非 None 时抛异常。"""
    calls = []

    def fake(messages, api_key, timeout=90, max_retries=2,
             heartbeat=None, token_usage=None):
        calls.append(messages)
        if side_effect is not None:
            side_effect()
        if error is not None:
            raise error
        if token_usage is not None:
            token_usage["total"] = token
        return content

    fake.calls = calls
    return fake


def _summary(content):
    return " ".join(content.split())[:60]


# ---- 成功链路 ----

def test_execute_one_success_writes_output_and_done(conn, tmp_path, monkeypatch):
    task_id = seed_task(conn)
    fake = make_chat_text()
    monkeypatch.setattr(llm, "chat_text", fake)

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is True
    assert result.token_used == 120
    assert result.conflict is False
    assert result.saved_output is True
    assert result.error is None
    assert len(fake.calls) == 1  # 一次 LLM 调用

    row = conn.execute(
        "SELECT version, filename, content, file_mtime, file_hash "
        "FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchone()
    assert row is not None
    assert row["version"] == 1
    assert row["filename"] == "output.md"
    assert row["content"] == CONTENT
    assert row["file_hash"] == hashlib.sha256(CONTENT.encode("utf-8")).hexdigest()
    assert row["file_mtime"] is not None

    out_file = tmp_path / "outputs" / "tasks" / str(task_id) / "output.md"
    assert out_file.is_file()
    assert out_file.read_text(encoding="utf-8") == CONTENT

    task = conn.execute(
        "SELECT status, completed_at, token_used, ai_summary FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert task["status"] == "done"
    assert task["completed_at"] is not None
    assert task["token_used"] == 120
    assert task["ai_summary"] == _summary(CONTENT)


def test_execute_one_accumulates_token_used_on_redo(conn, tmp_path, monkeypatch):
    task_id = seed_task(conn, status="waiting", token_used=100, fail_count=2)
    monkeypatch.setattr(llm, "chat_text", make_chat_text(token=80))

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is True
    assert result.token_used == 80
    row = conn.execute(
        "SELECT token_used, fail_count, status FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    assert row["token_used"] == 180  # 累加不清零
    assert row["fail_count"] == 2  # 成功不重置 fail_count（S5.1 redo 语义）
    assert row["status"] == "done"


def test_execute_one_updates_existing_output_row_in_place(conn, tmp_path, monkeypatch):
    task_id = seed_task(conn, status="waiting")  # redo 场景：已有产物
    seed_output(conn, task_id, "旧内容")
    monkeypatch.setattr(llm, "chat_text", make_chat_text())

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is True
    rows = conn.execute(
        "SELECT id, version, content FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchall()
    assert len(rows) == 1  # 不新增版本
    assert rows[0]["version"] == 1
    assert rows[0]["content"] == CONTENT  # 内容更新（外部回写语义）
    assert (tmp_path / "outputs" / "tasks" / str(task_id) / "output.md").is_file()


# ---- 失败回退 ----

def test_execute_one_llm_failure_rolls_back_to_waiting(conn, tmp_path, monkeypatch):
    task_id = seed_task(conn, fail_count=2)
    monkeypatch.setattr(
        llm, "chat_text", make_chat_text(error=ValueError("LLM down"))
    )

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is False
    assert "LLM down" in (result.error or "")
    assert result.conflict is False
    assert result.saved_output is False
    row = conn.execute(
        "SELECT status, fail_count, last_fail_reason FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "waiting"  # 回退 waiting
    assert row["fail_count"] == 3  # fail_count + 1
    assert "LLM down" in row["last_fail_reason"]
    assert conn.execute(
        "SELECT COUNT(*) FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 0  # 无产物


# ---- 幂等 ----

def test_execute_one_done_with_output_skips(conn, tmp_path, monkeypatch):
    task_id = seed_task(conn, status="done")
    seed_output(conn, task_id, "已有产物")
    fake = make_chat_text()
    monkeypatch.setattr(llm, "chat_text", fake)

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is True
    assert result.token_used == 0
    assert result.saved_output is False
    assert fake.calls == []  # 不调 LLM
    row = conn.execute(
        "SELECT status, token_used FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    assert row["status"] == "done"
    assert row["token_used"] == 0  # 不累加
    assert conn.execute(
        "SELECT content FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == "已有产物"  # 原产物不动


def test_execute_one_done_without_output_executes(conn, tmp_path, monkeypatch):
    task_id = seed_task(conn, status="done")  # done 无产物 → 补执行
    fake = make_chat_text()
    monkeypatch.setattr(llm, "chat_text", fake)

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is True
    assert len(fake.calls) == 1
    row = conn.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["status"] == "done"
    assert conn.execute(
        "SELECT COUNT(*) FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 1


# ---- 状态冲突 ----

def test_execute_one_claim_conflict_writes_output_without_status_change(
        conn, tmp_path, monkeypatch):
    task_id = seed_task(conn, status="in_progress")  # 已被其他执行器占用
    fake = make_chat_text()
    monkeypatch.setattr(llm, "chat_text", fake)

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is True
    assert result.conflict is True
    assert result.saved_output is True
    assert len(fake.calls) == 1  # 冲突仍执行 LLM
    row = conn.execute(
        "SELECT status, token_used, fail_count FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    assert row["status"] == "in_progress"  # 不覆盖外部状态
    assert row["token_used"] == 0  # 未累加（任务不属于本次执行）
    assert conn.execute(
        "SELECT content FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == CONTENT  # 产物仍写
    notif = conn.execute(
        "SELECT level, entity_type, entity_id FROM notifications "
        "WHERE entity_type = 'task' AND entity_id = ?", (task_id,)
    ).fetchone()
    assert notif is not None
    assert notif["level"] == "warn"  # warn 通知


def test_execute_one_completion_conflict_keeps_external_status(
        conn, tmp_path, monkeypatch):
    task_id = seed_task(conn, status="todo")

    def move_to_waiting():
        conn.execute(
            "UPDATE tasks SET status='waiting', updated_at=datetime('now') "
            "WHERE id = ?", (task_id,)
        )
        conn.commit()

    monkeypatch.setattr(
        llm, "chat_text", make_chat_text(side_effect=move_to_waiting)
    )

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is True
    assert result.conflict is True  # 完成期条件 UPDATE rowcount=0
    assert result.saved_output is True  # 产物已写
    row = conn.execute(
        "SELECT status, completed_at, token_used FROM tasks WHERE id = ?",
        (task_id,),
    ).fetchone()
    assert row["status"] == "waiting"  # 保留外部状态
    assert row["completed_at"] is None
    assert row["token_used"] == 0  # 未累加
    assert conn.execute(
        "SELECT content FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == CONTENT  # 产物仍写
    notif = conn.execute(
        "SELECT level FROM notifications WHERE entity_type = 'task' "
        "AND entity_id = ?", (task_id,)
    ).fetchone()
    assert notif is not None and notif["level"] == "warn"


def test_execute_one_conflict_path_llm_failure_touches_nothing(
        conn, tmp_path, monkeypatch):
    task_id = seed_task(conn, status="in_progress", fail_count=1)
    monkeypatch.setattr(
        llm, "chat_text", make_chat_text(error=RuntimeError("boom"))
    )

    result = executor.execute_one(conn, task_id, "sk-test",
                                  base_path=str(tmp_path))

    assert result.ok is False
    assert result.conflict is True
    row = conn.execute(
        "SELECT status, fail_count FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    assert row["status"] == "in_progress"  # 不碰状态
    assert row["fail_count"] == 1  # 不递增
    assert conn.execute(
        "SELECT COUNT(*) FROM outputs WHERE task_id = ?", (task_id,)
    ).fetchone()[0] == 0


# ---- 任务不存在 ----

def test_execute_one_task_not_found(conn, tmp_path):
    result = executor.execute_one(conn, 99999, "sk-test", base_path=str(tmp_path))
    assert result.ok is False
    assert "not found" in (result.error or "").lower()
    assert result.conflict is False
    assert result.saved_output is False
