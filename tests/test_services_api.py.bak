import pytest
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


def test_query_route_closes_sqlite_connection(tmp_path, monkeypatch):
    db_path = str(tmp_path / "closed.db")
    connections = []
    real_connect = db.connect

    class ConnectionSpy:
        def __init__(self, connection):
            self.connection = connection
            self.closed = False

        def close(self):
            self.closed = True
            self.connection.close()

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def tracking_connect(path):
        connection = ConnectionSpy(real_connect(path))
        connections.append(connection)
        return connection

    monkeypatch.setattr(server.db, "connect", tracking_connect)
    client = TestClient(server.create_app(db_path))

    assert client.get("/api/queues").status_code == 200
    assert len(connections) == 1
    assert connections[0].closed is True


def test_page_size_over_maximum_is_rejected(tmp_path):
    client = TestClient(server.create_app(str(tmp_path / "page-size.db")))

    response = client.get("/api/hotspots?page_size=101")

    assert response.status_code == 400
    assert response.json() == {"detail": "page_size must be <= 100"}


def test_collect_route_calls_collection_service(tmp_path, monkeypatch):
    db_path = str(tmp_path / "collect-route.db")
    expected = {"collected": 1, "discarded": 0, "review": 0, "errors": []}
    calls = []

    def fake_collect(db_arg, base_path):
        calls.append((db_arg, base_path))
        return expected

    monkeypatch.setattr(server.services, "collect_ideas", fake_collect)
    client = TestClient(server.create_app(db_path))

    response = client.post("/api/collect")

    assert response.status_code == 200
    assert response.json() == expected
    assert calls == [(db_path, server.pathlib.Path(server.__file__).parent.parent)]
