"""Tests for the Idea Hub FastAPI app factory (S1.3)."""

from fastapi.testclient import TestClient

from idea_hub.config import Config
from idea_hub.errors import AppError
from idea_hub.main import create_app

AUTH = ("admin", "secret")


def make_config(tmp_path, *, rate_limit_per_min=60, auth_user="admin", auth_pass="secret"):
    return Config(
        host="127.0.0.1",
        port=8000,
        db_path=str(tmp_path / "test.db"),
        base_path=str(tmp_path),
        auth_user=auth_user,
        auth_pass=auth_pass,
        deepseek_api_key="",
        rate_limit_per_min=rate_limit_per_min,
        log_level="INFO",
    )


def client_for(config):
    return TestClient(create_app(config))


def test_health_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/health")
    assert resp.status_code == 401


def test_health_with_auth_200(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/health", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {
        "data": {
            "status": "ok",
            "db": "ok",
            "scheduler": {"last_tick": None, "status": "never_run"},
        }
    }


def test_health_wrong_credentials_401(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/health", auth=("admin", "wrong"))
    assert resp.status_code == 401


def test_rate_limit_429(tmp_path):
    client = client_for(make_config(tmp_path, rate_limit_per_min=2))
    assert client.get("/api/v1/health", auth=AUTH).status_code == 200
    assert client.get("/api/v1/health", auth=AUTH).status_code == 200
    resp = client.get("/api/v1/health", auth=AUTH)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


def test_unknown_error_500_shape(tmp_path):
    app = create_app(make_config(tmp_path))

    @app.get("/boom")
    def boom():
        raise RuntimeError("boom")

    resp = TestClient(app).get("/boom", auth=AUTH)
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "INTERNAL"


def test_app_error_shape(tmp_path):
    app = create_app(make_config(tmp_path))

    @app.get("/bad")
    def bad():
        raise AppError(status_code=400, code="BAD_REQUEST", message="bad request")

    resp = TestClient(app).get("/bad", auth=AUTH)
    assert resp.status_code == 400
    assert resp.json() == {"error": {"code": "BAD_REQUEST", "message": "bad request"}}


def test_cors_dev_origin_allowed(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get(
        "/api/v1/health",
        auth=AUTH,
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_spa_fallback_serves_index_html(tmp_path):
    static_dir = tmp_path / "web"
    static_dir.mkdir()
    index_html = "<!doctype html><html><head><meta charset=\"utf-8\"></head><body><div id=\"root\"></div></body></html>"
    (static_dir / "index.html").write_text(index_html, encoding="utf-8")
    assets_dir = static_dir / "assets"
    assets_dir.mkdir()
    app_js = 'console.log("hi")'
    (assets_dir / "app.js").write_text(app_js, encoding="utf-8")

    client = TestClient(create_app(make_config(tmp_path), static_dir=str(static_dir)))

    resp = client.get("/kanban")
    assert resp.status_code == 200
    assert '<div id="root">' in resp.text
    resp = client.get("/assets/app.js")
    assert resp.status_code == 200
    assert resp.text == app_js


def test_spa_fallback_does_not_shadow_api(tmp_path):
    static_dir = tmp_path / "web"
    static_dir.mkdir()
    (static_dir / "index.html").write_text(
        "<!doctype html><html><body><div id=\"root\"></div></body></html>",
        encoding="utf-8",
    )

    client = TestClient(create_app(make_config(tmp_path), static_dir=str(static_dir)))

    assert client.get("/api/v1/health", auth=AUTH).status_code == 200
    assert client.get("/api/v1/nonexistent-route").status_code == 404
