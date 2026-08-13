"""端到端：waiting → 调度（mock API）→ 质检通过 → done → 通知记录。"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from idea_hub import db, executor, models, scheduler


def test_full_chain(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    conn.execute("INSERT INTO targets (id, name, description, score_dimensions, is_active) "
                 "VALUES (1, '默认', '', '{}', 1)")
    conn.commit()
    tid = models.create_task(conn, title="端到端任务", idea_summary="s",
                             target_id=1, feasibility_score=9, score_breakdown="{}",
                             content_type="short")
    models.move_task(conn, tid, "waiting")
    conn.commit()

    payload = json.dumps({"title": "标题", "content": "短文内容" * 60, "word_count": 240})
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload, 500), (qa_ok, 200)]
    with patch("idea_hub.executor.call_llm", side_effect=lambda p, **k: seq.pop(0)), \
         patch("subprocess.Popen"), \
         patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert r["claimed"] == [tid]
    # 模拟子进程执行
    seq2 = [(payload, 500), (qa_ok, 200)]
    with patch("idea_hub.executor.call_llm", side_effect=lambda p, **k: seq2.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    task = models.get_task(conn, tid)
    assert task["status"] == "done"
    assert (Path(tmp_path) / "outputs" / "tasks" / str(tid) / "output.md").exists()
    assert task["token_used"] >= 700
    # 通知闭环：执行器 _complete_task 已自动发 done 通知（接线修复），
    # 此处再手动补一条验证表可查，并断言 done 类型记录存在
    models.create_notification(conn, task_id=tid, type="done", title="完成", body="摘要")
    notes = models.list_notifications(conn)
    assert any(n["type"] == "done" and n["task_id"] == tid for n in notes)
    assert len(notes) == 2
