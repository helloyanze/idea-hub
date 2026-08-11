import json, pytest
from fastapi.testclient import TestClient
from idea_hub import db, models, server

@pytest.fixture  # noqa: F401 (used via conftest pattern; define locally)
def client(tmp_path):
    db_path = str(tmp_path / "api.db")
    conn = db.connect(db_path); db.init_schema(conn)
    models.create_target(conn, name="自媒体", description="d", score_dimensions="{}")
    models.activate_target(conn, 1)
    models.create_source(conn, type="hotlist", name="榜", url="http://x")
    conn.close()
    app = server.create_app(db_path)
    return TestClient(app)

def test_stats_and_task_lifecycle(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = {"title": "想法", "idea_summary": "s", "target_id": 1, "hot_item_id": None,
            "feasibility_score": 8, "score_breakdown": "{}", "idea_path": ""}
    r = client.post("/api/tasks", json=body)
    assert r.status_code == 200
    tid = r.json()["id"]
    r = client.post(f"/api/tasks/{tid}/move", json={"to_status": "waiting"})
    assert r.json()["status"] == "waiting"
    r = client.patch(f"/api/tasks/{tid}", json={"notes": "用户备注"})
    assert r.json()["notes"] == "用户备注"

def test_execute_request_created(client):
    body = {"title": "t", "idea_summary": "s", "target_id": 1, "hot_item_id": None,
            "feasibility_score": 7, "score_breakdown": "{}", "idea_path": ""}
    tid = client.post("/api/tasks", json=body).json()["id"]
    r = client.post(f"/api/tasks/{tid}/execute")
    assert r.status_code == 200
    conn = db.connect(client.app.state.db_path)
    row = conn.execute("SELECT status FROM execute_requests WHERE task_id=?", (tid,)).fetchone()
    assert row["status"] == "pending"
    conn.close()

def test_target_activate(client):
    r = client.post("/api/targets", json={"name": "开发类", "description": "d",
                                          "score_dimensions": "{}"})
    assert r.status_code == 200
    tid = r.json()["id"]
    client.post(f"/api/targets/{tid}/activate")
    items = client.get("/api/targets").json()["items"]
    assert sum(t["is_active"] for t in items) == 1
    assert next(t for t in items if t["id"] == tid)["is_active"] == 1
