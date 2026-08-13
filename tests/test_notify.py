"""通知模块：hermes send 子进程调用（mock）+ notifications 表双写。"""
import sqlite3
from pathlib import Path
from unittest.mock import patch
from idea_hub import db, models, notify

def test_send_writes_notification_and_calls_send(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)  # 项目约定：新建库需先建表（同 conftest conn fixture）
    calls = []
    def fake_run(cmd, capture_output=True, text=True, timeout=30):
        calls.append(cmd)
        class R: returncode = 0
        return R()
    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    notify.send(conn, task_id=1, type="done", title="完成", body="摘要",
                qq_target="qq:123")
    rows = models.list_notifications(conn)
    assert len(rows) == 1 and rows[0]["type"] == "done"
    assert any("hermes" in str(c) and "qq:123" in str(c) for c in calls)

def test_send_without_target_skips_send(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)  # 项目约定：新建库需先建表（同 conftest conn fixture）
    called = []
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: called.append(a))
    notify.send(conn, task_id=None, type="scheduler", title="t", body="b", qq_target=None)
    assert called == []

def test_send_failure_does_not_raise(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)  # 项目约定：新建库需先建表（同 conftest conn fixture）
    def boom(*a, **k):
        raise FileNotFoundError("hermes not installed")
    monkeypatch.setattr(notify.subprocess, "run", boom)
    notify.send(conn, task_id=1, type="done", title="t", body="b", qq_target="qq:1")
    assert len(models.list_notifications(conn)) == 1  # 表已写，不抛异常
