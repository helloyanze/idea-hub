from fastapi.testclient import TestClient

from idea_hub import db, models, server


def test_structured_menu_routes_and_generate_status(tmp_path, monkeypatch):
    db_path = str(tmp_path / "api.db")
    conn = db.connect(db_path)
    db.init_schema(conn)
    target_id = models.create_target(conn, name="Target", description="", score_dimensions="{}")
    models.activate_target(conn, target_id)
    source_id = models.create_source(conn, type="hotlist", name="Source", url="http://x")
    conn.execute(
        "INSERT INTO hot_items (source_id, title, url) VALUES (?,?,?)",
        (source_id, "Hot", "http://hot"),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr("idea_hub.collectors.collect_all", lambda conn: {
        "collected": 0, "discarded": 0, "review": 0, "errors": []
    })

    client = TestClient(server.create_app(db_path))

    assert client.get("/api/hotspots?page=1&page_size=10").json()["total"] == 1
    assert client.get("/api/queues").json() == {
        "archived": 0, "todo": 0, "waiting": 0, "in_progress": 0, "done": 0
    }
    assert client.get("/api/queues/bogus").status_code == 400
    assert client.get("/api/queues/todo?page=2&page_size=10").json() == {
        "items": [], "total": 0, "page": 2, "page_size": 10
    }
    response = client.post("/api/generate")
    assert response.status_code == 200
    assert response.json()["status"] == "needs-agent"
