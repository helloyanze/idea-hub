"""API：通知读写、健康状态、任务新字段、重置失败计数。"""
from fastapi.testclient import TestClient
from idea_hub import db, models, server


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "t.db"))
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    models.create_target(conn, name="t", description="", score_dimensions="{}")
    return TestClient(server.create_app(str(tmp_path / "t.db"))), conn

def test_notifications_api(tmp_path, monkeypatch):
    client, conn = _client(tmp_path, monkeypatch)
    models.create_notification(conn, task_id=None, type="done", title="t", body="b")
    r = client.get("/api/notifications")
    assert r.status_code == 200
    data = r.json()
    assert data["items"][0]["title"] == "t" and data["unread"] == 1
    nid = data["items"][0]["id"]
    client.post(f"/api/notifications/{nid}/read")
    assert client.get("/api/notifications").json()["unread"] == 0

def test_health_api(tmp_path, monkeypatch):
    client, conn = _client(tmp_path, monkeypatch)
    models.set_setting(conn, "last_scheduler_tick", "2026-08-13T00:00:00")
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert "last_tick" in d and "minutes_ago" in d and "today_tokens" in d

def test_patch_task_new_fields(tmp_path, monkeypatch):
    client, conn = _client(tmp_path, monkeypatch)
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    r = client.patch(f"/api/tasks/{tid}", json={"content_type": "short",
                                                "is_complex": 1,
                                                "redo_note": "口语化"})
    assert r.status_code == 200
    t = models.get_task(conn, tid)
    assert t["content_type"] == "short" and t["is_complex"] == 1
    assert t["redo_note"] == "口语化"

def test_reset_failures(tmp_path, monkeypatch):
    client, conn = _client(tmp_path, monkeypatch)
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    models.update_task(conn, tid, fail_count=3, last_fail_reason="x")
    r = client.post(f"/api/tasks/{tid}/reset-failures")
    assert r.status_code == 200
    t = models.get_task(conn, tid)
    assert t["fail_count"] == 0 and t["last_fail_reason"] is None
