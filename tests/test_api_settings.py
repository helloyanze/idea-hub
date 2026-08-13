"""Tests for the Idea Hub settings API (S1.5).

Covers: GET /api/v1/settings returns only the 7 user-configurable keys
(internal keys never exposed), PUT /api/v1/settings type validation
(INVALID_SETTING_VALUE), unknown key rejection (UNKNOWN_SETTING), and
BasicAuth protection on both endpoints.
"""
from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.config import Config
from idea_hub.main import create_app

AUTH = ("admin", "secret")

PUBLIC_KEYS = {
    "score_todo_threshold": 8,
    "collect_interval_hours": 24,
    "daily_budget_tokens": 50000,
    "score_dimensions": ["facts", "verification", "timeliness", "value"],
    "generate_count": 10,
    "done_column_limit": 50,
    "discard_retention_days": 7,
}

INTERNAL_KEYS = ("scheduler_last_tick", "scheduler_last_collect")


def make_config(tmp_path, *, auth_user="admin", auth_pass="secret"):
    return Config(
        host="127.0.0.1",
        port=8000,
        db_path=str(tmp_path / "test.db"),
        base_path=str(tmp_path),
        auth_user=auth_user,
        auth_pass=auth_pass,
        deepseek_api_key="",
        rate_limit_per_min=60,
        log_level="INFO",
    )


def client_for(config):
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    conn.close()
    return TestClient(create_app(config))


def test_get_settings_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/settings")
    assert resp.status_code == 401


def test_get_settings_returns_public_keys_only(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/settings", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["data"].keys()) == set(PUBLIC_KEYS)
    assert not set(INTERNAL_KEYS) & set(body["data"].keys())


def test_get_settings_values_converted_by_type(tmp_path):
    client = client_for(make_config(tmp_path))
    data = client.get("/api/v1/settings", auth=AUTH).json()["data"]
    assert data == PUBLIC_KEYS
    assert isinstance(data["score_todo_threshold"], int)
    assert isinstance(data["score_dimensions"], list)


def test_put_settings_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.put("/api/v1/settings", json={"key": "score_todo_threshold", "value": 12})
    assert resp.status_code == 401


def test_put_settings_updates_int_key(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.put(
        "/api/v1/settings", auth=AUTH, json={"key": "score_todo_threshold", "value": 12}
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"key": "score_todo_threshold", "value": 12}
    data = client.get("/api/v1/settings", auth=AUTH).json()["data"]
    assert data["score_todo_threshold"] == 12


def test_put_settings_accepts_numeric_string_for_int_key(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.put(
        "/api/v1/settings", auth=AUTH, json={"key": "generate_count", "value": "15"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"key": "generate_count", "value": 15}


def test_put_settings_invalid_value_400(tmp_path):
    client = client_for(make_config(tmp_path))
    for bad in ("not-a-number", 8.5, True, None, ["list"]):
        resp = client.put(
            "/api/v1/settings", auth=AUTH, json={"key": "score_todo_threshold", "value": bad}
        )
        assert resp.status_code == 400, f"value={bad!r}"
        assert resp.json()["error"]["code"] == "INVALID_SETTING_VALUE", f"value={bad!r}"


def test_put_settings_unknown_key_400(tmp_path):
    client = client_for(make_config(tmp_path))
    for key in ("scheduler_last_tick", "scheduler_last_collect", "totally_unknown"):
        resp = client.put("/api/v1/settings", auth=AUTH, json={"key": key, "value": "x"})
        assert resp.status_code == 400, key
        assert resp.json()["error"]["code"] == "UNKNOWN_SETTING", key


def test_put_settings_json_key_validation(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.put(
        "/api/v1/settings", auth=AUTH, json={"key": "score_dimensions", "value": ["a", "b"]}
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"key": "score_dimensions", "value": ["a", "b"]}
    bad = client.put(
        "/api/v1/settings", auth=AUTH, json={"key": "score_dimensions", "value": "not-a-list"}
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "INVALID_SETTING_VALUE"
