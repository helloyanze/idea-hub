"""Tests for /health scheduler status sourced from settings (S5.5).

The health endpoint must read the internal key scheduler_last_tick and
report never_run (key missing), running (fresh tick), or unhealthy (tick
older than 10 minutes).
"""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.config import Config
from idea_hub.main import create_app

AUTH = ("admin", "secret")


def make_config(tmp_path, **overrides):
    values = dict(
        host="127.0.0.1",
        port=8000,
        db_path=str(tmp_path / "test.db"),
        base_path=str(tmp_path),
        auth_user="admin",
        auth_pass="secret",
        deepseek_api_key="",
        rate_limit_per_min=60,
        log_level="INFO",
    )
    values.update(overrides)
    return Config(**values)


def _write_last_tick(config, when: str):
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES ('scheduler_last_tick', ?, 'string')",
        (when,),
    )
    conn.commit()
    conn.close()


def _scheduler_body(tmp_path, last_tick: str | None):
    config = make_config(tmp_path)
    if last_tick is not None:
        _write_last_tick(config, last_tick)
    else:
        conn = db.connect(config.db_path)
        db.init_schema(conn)
        conn.close()
    resp = TestClient(create_app(config)).get("/api/v1/health", auth=AUTH)
    assert resp.status_code == 200
    return resp.json()["data"]["scheduler"]


def test_health_never_run_without_last_tick(tmp_path):
    body = _scheduler_body(tmp_path, last_tick=None)
    assert body["status"] == "never_run"
    assert body["last_tick"] is None


def test_health_running_with_fresh_last_tick(tmp_path):
    fresh = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    body = _scheduler_body(tmp_path, last_tick=fresh)
    assert body["status"] == "running"
    assert body["last_tick"] is not None


def test_health_unhealthy_with_stale_last_tick(tmp_path):
    stale = (
        datetime.now(timezone.utc) - timedelta(minutes=15)
    ).strftime("%Y-%m-%d %H:%M:%S")
    body = _scheduler_body(tmp_path, last_tick=stale)
    assert body["status"] == "unhealthy"
    assert body["last_tick"] is not None
