"""Tests for the Idea Hub scheduler tick (S5.5).

Covers: expired task auto-completion (todo/waiting -> done, in_progress
skipped), discard cleanup honoring settings.discard_retention_days with FTS
sync, stale running-job recovery (conditional UPDATE, heartbeat timeout),
collect trigger via local HTTP API (interval, dedup, auth, failure handling),
internal key persistence (scheduler_last_tick / scheduler_last_collect), and
the TickResult contract.
"""
from datetime import datetime, timedelta, timezone

import pytest

from idea_hub import db, scheduler
from idea_hub.config import Config


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _past_str(minutes: int) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(minutes=minutes)
    ).strftime("%Y-%m-%d %H:%M:%S")


def make_config(tmp_path, **overrides):
    values = dict(
        host="127.0.0.1",
        port=8000,
        db_path=str(tmp_path / "test.db"),
        base_path=str(tmp_path),
        auth_user="",
        auth_pass="",
        deepseek_api_key="",
        rate_limit_per_min=60,
        log_level="INFO",
    )
    values.update(overrides)
    return Config(**values)


class _OkResponse:
    ok = True
    status_code = 200


@pytest.fixture()
def quiet(conn):
    """Set scheduler_last_collect to now so unrelated ticks skip the HTTP call."""
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES ('scheduler_last_collect', ?, 'string')",
        (_now_str(),),
    )
    conn.commit()


def test_tick_result_shape(conn, quiet, tmp_path):
    result = scheduler.tick(conn, make_config(tmp_path))
    assert set(result.keys()) == {
        "expired_count",
        "cleaned_count",
        "recovered_count",
        "collect_triggered",
    }


def test_tick_updates_scheduler_last_tick(conn, quiet, tmp_path):
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM settings WHERE key = 'scheduler_last_tick'"
        ).fetchone()[0]
        == 0
    )
    scheduler.tick(conn, make_config(tmp_path))
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'scheduler_last_tick'"
    ).fetchone()
    assert row is not None and row[0]


def test_tick_expires_todo_and_waiting_tasks(conn, quiet, tmp_path):
    old = _past_str(60)
    future = (
        datetime.now(timezone.utc) + timedelta(hours=2)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO tasks (title, status, expire_at) VALUES ('t1', 'todo', ?)",
        (old,),
    )
    conn.execute(
        "INSERT INTO tasks (title, status, expire_at) VALUES ('t2', 'waiting', ?)",
        (old,),
    )
    conn.execute(
        "INSERT INTO tasks (title, status, expire_at) VALUES ('t3', 'todo', ?)",
        (future,),
    )
    conn.commit()

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["expired_count"] == 2
    rows = {
        r["title"]: r
        for r in conn.execute(
            "SELECT title, status, completed_at, notes FROM tasks"
        ).fetchall()
    }
    assert rows["t1"]["status"] == "done"
    assert rows["t1"]["completed_at"]
    assert "过期" in rows["t1"]["notes"]
    assert rows["t2"]["status"] == "done"
    assert rows["t2"]["completed_at"]
    assert rows["t3"]["status"] == "todo"

    notifs = conn.execute(
        "SELECT type, entity_type, entity_id FROM notifications "
        "WHERE type = 'task_expired'"
    ).fetchall()
    assert len(notifs) == 2
    assert all(n["entity_type"] == "task" for n in notifs)


def test_tick_expires_same_day_past_task_only(conn, quiet, tmp_path):
    past = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).strftime("%Y-%m-%d %H:%M:%S")
    tomorrow = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).strftime("%Y-%m-%d %H:%M:%S")
    past_task_id = conn.execute(
        "INSERT INTO tasks (title, status, expire_at) "
        "VALUES ('past-today', 'todo', ?)",
        (past,),
    ).lastrowid
    conn.execute(
        "INSERT INTO tasks (title, status, expire_at) "
        "VALUES ('tomorrow', 'todo', ?)",
        (tomorrow,),
    )
    conn.commit()

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["expired_count"] == 1
    rows = {
        row["title"]: row["status"]
        for row in conn.execute("SELECT title, status FROM tasks").fetchall()
    }
    assert rows["past-today"] == "done"
    assert rows["tomorrow"] == "todo"
    notification = conn.execute(
        "SELECT type, entity_type, entity_id FROM notifications "
        "WHERE type = 'task_expired'"
    ).fetchone()
    assert notification["type"] == "task_expired"
    assert notification["entity_type"] == "task"
    assert notification["entity_id"] == past_task_id


def test_tick_expires_legacy_t_format_task(conn, quiet, tmp_path):
    legacy_expire_at = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%S")
    task_id = conn.execute(
        "INSERT INTO tasks (title, status, expire_at) "
        "VALUES ('legacy-t-format', 'todo', ?)",
        (legacy_expire_at,),
    ).lastrowid
    conn.commit()

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["expired_count"] == 1
    row = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    assert row["status"] == "done"
    notification = conn.execute(
        "SELECT type FROM notifications "
        "WHERE type = 'task_expired' AND entity_id = ?",
        (task_id,),
    ).fetchone()
    assert notification["type"] == "task_expired"


def test_tick_skips_in_progress_expired_task(conn, quiet, tmp_path):
    old = _past_str(60)
    conn.execute(
        "INSERT INTO tasks (title, status, expire_at) "
        "VALUES ('t1', 'in_progress', ?)",
        (old,),
    )
    conn.commit()

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["expired_count"] == 0
    row = conn.execute("SELECT status FROM tasks WHERE title = 't1'").fetchone()
    assert row["status"] == "in_progress"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE type = 'task_expired'"
        ).fetchone()[0]
        == 0
    )


def test_tick_cleans_discard_items_over_retention(conn, quiet, tmp_path):
    conn.execute("UPDATE settings SET value = '3' WHERE key = 'discard_retention_days'")
    conn.execute(
        "INSERT INTO sources (type, name, url) VALUES ('rss', 'src1', 'http://x')"
    )
    conn.execute(
        "INSERT INTO hot_items (source_id, title, url, verdict, collected_date) "
        "VALUES (1, 'old discard', 'u1', 'discard', date('now', '-3 days'))"
    )
    conn.execute(
        "INSERT INTO hot_items (source_id, title, url, verdict, collected_date) "
        "VALUES (1, 'recent discard', 'u2', 'discard', date('now', '-2 days'))"
    )
    conn.execute(
        "INSERT INTO hot_items (source_id, title, url, verdict, collected_date) "
        "VALUES (1, 'old admit', 'u3', 'admit', date('now', '-3 days'))"
    )
    conn.commit()

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["cleaned_count"] == 1
    titles = {
        r["title"]
        for r in conn.execute("SELECT title FROM hot_items").fetchall()
    }
    assert "old discard" not in titles
    assert "recent discard" in titles
    assert "old admit" in titles

    # FTS index must be kept in sync after the DELETE (contentless triggers).
    gone = conn.execute(
        "SELECT COUNT(*) FROM hot_items_fts WHERE hot_items_fts MATCH ?",
        ('"old discard"',),
    ).fetchone()[0]
    assert gone == 0
    kept = conn.execute(
        "SELECT COUNT(*) FROM hot_items_fts WHERE hot_items_fts MATCH ?",
        ('"recent discard"',),
    ).fetchone()[0]
    assert kept == 1


def test_tick_recovers_stale_running_jobs(conn, quiet, tmp_path):
    conn.execute(
        "INSERT INTO jobs (type, status, heartbeat_at, created_at) "
        "VALUES ('collect', 'running', ?, datetime('now'))",
        (_past_str(6 * 60),),
    )
    conn.execute(
        "INSERT INTO jobs (type, status, heartbeat_at, created_at) "
        "VALUES ('generate', 'running', ?, datetime('now'))",
        (_now_str(),),
    )
    conn.execute(
        "INSERT INTO jobs (type, status, heartbeat_at, created_at) "
        "VALUES ('execute', 'running', NULL, ?)",
        (_past_str(6 * 60),),
    )
    conn.commit()

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["recovered_count"] == 2
    by_type = {
        r["type"]: r
        for r in conn.execute("SELECT type, status, error FROM jobs").fetchall()
    }
    assert by_type["collect"]["status"] == "failed"
    assert by_type["collect"]["error"] == "stale: heartbeat timeout"
    assert by_type["generate"]["status"] == "running"
    assert by_type["execute"]["status"] == "failed"
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE type = 'job_failed'"
        ).fetchone()[0]
        == 2
    )


def test_tick_triggers_collect_when_interval_elapsed(conn, monkeypatch, tmp_path):
    old = _past_str(25 * 60)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES ('scheduler_last_collect', ?, 'string')",
        (old,),
    )
    conn.commit()
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return _OkResponse()

    monkeypatch.setattr(scheduler.requests, "post", fake_post)

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["collect_triggered"] is True
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "http://127.0.0.1:8000/api/v1/collect"
    assert kwargs.get("auth") is None
    new_value = conn.execute(
        "SELECT value FROM settings WHERE key = 'scheduler_last_collect'"
    ).fetchone()[0]
    assert new_value != old


def test_tick_collect_not_triggered_within_interval(conn, monkeypatch, tmp_path):
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES ('scheduler_last_collect', ?, 'string')",
        (_now_str(),),
    )
    conn.commit()
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _OkResponse()

    monkeypatch.setattr(scheduler.requests, "post", fake_post)

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["collect_triggered"] is False
    assert calls == []


def test_tick_collect_first_tick_when_key_missing(conn, monkeypatch, tmp_path):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _OkResponse()

    monkeypatch.setattr(scheduler.requests, "post", fake_post)

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["collect_triggered"] is True
    assert len(calls) == 1
    row = conn.execute(
        "SELECT value FROM settings WHERE key = 'scheduler_last_collect'"
    ).fetchone()
    assert row is not None and row[0]


def test_tick_collect_skipped_when_running_collect_job(conn, monkeypatch, tmp_path):
    old = _past_str(25 * 60)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES ('scheduler_last_collect', ?, 'string')",
        (old,),
    )
    conn.execute(
        "INSERT INTO jobs (type, status, heartbeat_at) "
        "VALUES ('collect', 'running', ?)",
        (_now_str(),),
    )
    conn.commit()
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        return _OkResponse()

    monkeypatch.setattr(scheduler.requests, "post", fake_post)

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["collect_triggered"] is False
    assert calls == []
    assert (
        conn.execute(
            "SELECT value FROM settings WHERE key = 'scheduler_last_collect'"
        ).fetchone()[0]
        == old
    )


def test_tick_collect_failure_does_not_update_last_collect(conn, monkeypatch, tmp_path):
    old = _past_str(25 * 60)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES ('scheduler_last_collect', ?, 'string')",
        (old,),
    )
    conn.commit()

    def fake_post(url, **kwargs):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(scheduler.requests, "post", fake_post)

    result = scheduler.tick(conn, make_config(tmp_path))

    assert result["collect_triggered"] is False
    assert (
        conn.execute(
            "SELECT value FROM settings WHERE key = 'scheduler_last_collect'"
        ).fetchone()[0]
        == old
    )


def test_tick_collect_sends_basic_auth_when_configured(conn, monkeypatch, tmp_path):
    old = _past_str(25 * 60)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES ('scheduler_last_collect', ?, 'string')",
        (old,),
    )
    conn.commit()
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return _OkResponse()

    monkeypatch.setattr(scheduler.requests, "post", fake_post)

    scheduler.tick(
        conn, make_config(tmp_path, auth_user="admin", auth_pass="secret")
    )

    assert captured["auth"] == ("admin", "secret")
