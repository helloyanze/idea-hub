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

# ---- S2: 异常映射（move 400 / activate 404 / delete_source 404） ----

def test_move_invalid_status_400(client):
    body = {"title": "t", "idea_summary": "s", "target_id": 1, "hot_item_id": None,
            "feasibility_score": 7, "score_breakdown": "{}", "idea_path": ""}
    tid = client.post("/api/tasks", json=body).json()["id"]
    r = client.post(f"/api/tasks/{tid}/move", json={"to_status": "bogus"})
    assert r.status_code == 400

def test_activate_missing_target_404(client):
    r = client.post("/api/targets/999/activate")
    assert r.status_code == 404

def test_delete_missing_source_404(client):
    r = client.delete("/api/sources/999")
    assert r.status_code == 404

# ---- S3: FK 启用 + delete_source 级联删除 ----

def test_delete_source_cascades_hot_items(client):
    sid = client.post("/api/sources", json={"type": "rss", "name": "RSS源",
                                            "url": "http://rss"}).json()["id"]
    conn = db.connect(client.app.state.db_path)
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (?, 'R1', 'http://r1')", (sid,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM hot_items WHERE source_id=?", (sid,)).fetchone()[0] == 1
    conn.close()
    r = client.delete(f"/api/sources/{sid}")
    assert r.status_code == 200
    conn = db.connect(client.app.state.db_path)
    assert conn.execute("SELECT COUNT(*) FROM hot_items WHERE source_id=?", (sid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sources WHERE id=?", (sid,)).fetchone()[0] == 0
    conn.close()

def test_delete_source_clears_task_hot_item_ref(client):
    """回归：任务经 tasks.hot_item_id 引用来源热点时删除来源——task 保留且 hot_item_id 置 NULL"""
    sid = client.post("/api/sources", json={"type": "rss", "name": "RSS源",
                                            "url": "http://rss"}).json()["id"]
    conn = db.connect(client.app.state.db_path)
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (?, 'R1', 'http://r1')", (sid,))
    conn.commit()
    hid = conn.execute("SELECT id FROM hot_items WHERE source_id=?", (sid,)).fetchone()["id"]
    conn.close()
    body = {"title": "t", "idea_summary": "s", "target_id": 1, "hot_item_id": hid,
            "feasibility_score": 7, "score_breakdown": "{}", "idea_path": ""}
    tid = client.post("/api/tasks", json=body).json()["id"]
    r = client.delete(f"/api/sources/{sid}")
    assert r.status_code == 200
    conn = db.connect(client.app.state.db_path)
    assert conn.execute("SELECT COUNT(*) FROM hot_items WHERE source_id=?", (sid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sources WHERE id=?", (sid,)).fetchone()[0] == 0
    task = conn.execute("SELECT id, hot_item_id FROM tasks WHERE id=?", (tid,)).fetchone()
    assert task is not None and task["hot_item_id"] is None
    conn.close()

def test_delete_task_cascades(client):
    """删除任务：任务行 + 关联表清理，404 对不存在 id。"""
    # 准备：目标 + 热点 + 任务 + 标签 + 执行请求
    import json as _json
    client.post("/api/targets", json={"name": "t", "description": "d",
                                      "score_dimensions": "{}"})
    client.post("/api/targets/1/activate")
    r = client.post("/api/tasks", json={"title": "待删任务", "idea_summary": "s",
                                        "target_id": 1, "feasibility_score": 7,
                                        "score_breakdown": "{}", "idea_path": "",
                                        "notes": ""})
    tid = r.json()["id"]
    client.post(f"/api/tasks/{tid}/tags", json={"name": "AI"})
    client.post(f"/api/tasks/{tid}/execute")
    r = client.delete(f"/api/tasks/{tid}")
    assert r.status_code == 200
    assert client.get(f"/api/tasks/{tid}").status_code == 404
    assert client.delete("/api/tasks/9999").status_code == 404

def test_settings_auto_run_roundtrip(client):
    client.put("/api/settings", json={"key": "auto_run", "value": "0"})
    items = client.get("/api/settings").json()["items"]
    assert {"key": "auto_run", "value": "0"} in items
    client.put("/api/settings", json={"key": "auto_run", "value": "1"})

def test_auth_required_when_enabled(monkeypatch):
    """启用认证环境变量后：无凭证 401，错误凭证 401，正确凭证放行。"""
    import base64
    from idea_hub import server as server_mod
    monkeypatch.setenv("IDEAHUB_AUTH_USER", "idea")
    monkeypatch.setenv("IDEAHUB_AUTH_PASS", "secret")
    # 重新加载模块级常量
    import importlib; importlib.reload(server_mod)
    try:
        app = server_mod.create_app(str(tmp_path_for_auth()))
        client = TestClient(app)
        assert client.get("/").status_code == 401
        assert client.get("/api/queues", headers={"Authorization": "Basic " + base64.b64encode(b"idea:wrong").decode()}).status_code == 401
        ok = client.get("/", headers={"Authorization": "Basic " + base64.b64encode(b"idea:secret").decode()})
        assert ok.status_code == 200
    finally:
        monkeypatch.delenv("IDEAHUB_AUTH_USER", raising=False)
        monkeypatch.delenv("IDEAHUB_AUTH_PASS", raising=False)
        importlib.reload(server_mod)

def tmp_path_for_auth():
    import tempfile
    return tempfile.mkdtemp()
