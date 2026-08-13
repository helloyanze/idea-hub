"""Tests for the Idea Hub jobs service (Task S2.5).

Covers: job lifecycle (create -> running -> done), conditional UPDATEs
(heartbeat / progress / finish only apply while the job is running,
rowcount-checked), per-type dedup of running jobs, and the async collect
job's failure path (finish failed + job_failed notification).
"""
import pytest

from idea_hub import db
from idea_hub.errors import AppError
from idea_hub.services import jobs


def _get(conn, job_id):
    return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def test_job_lifecycle(conn):
    job_id = jobs.create_job(conn, "collect", {"source_ids": [1]})
    assert job_id > 0
    assert _get(conn, job_id)["status"] == "pending"

    assert jobs.mark_running(job_id) == 1
    row = _get(conn, job_id)
    assert row["status"] == "running"
    assert row["heartbeat_at"] is not None

    assert jobs.update_progress(job_id, 40) == 1
    assert _get(conn, job_id)["progress"] == 40

    assert jobs.heartbeat(job_id) == 1

    assert (
        jobs.finish(job_id, "done", result_ref='{"hotspot_count": 3}', token_used=100)
        == 1
    )
    row = _get(conn, job_id)
    assert row["status"] == "done"
    assert row["result_ref"] == '{"hotspot_count": 3}'
    assert row["token_used"] == 100


def test_job_heartbeat_and_failed_condition_update(conn):
    job_id = jobs.create_job(conn, "collect", {})
    # Pending: heartbeat / progress / finish are conditional no-ops.
    assert jobs.heartbeat(job_id) == 0
    assert jobs.update_progress(job_id, 50) == 0
    assert jobs.finish(job_id, "done") == 0
    assert _get(conn, job_id)["status"] == "pending"

    # Running: updates apply.
    assert jobs.mark_running(job_id) == 1
    assert jobs.heartbeat(job_id) == 1
    assert jobs.update_progress(job_id, 50) == 1

    # Status changed out from under us -> finish rowcount is 0.
    conn.execute("UPDATE jobs SET status = 'failed' WHERE id = ?", (job_id,))
    conn.commit()
    assert jobs.finish(job_id, "done", result_ref="x") == 0
    row = _get(conn, job_id)
    assert row["status"] == "failed"
    assert row["result_ref"] is None


def test_dedup_running(conn):
    job_id = jobs.create_job(conn, "collect", {})
    assert jobs.dedup_running(conn, "collect") is None  # pending is not running

    jobs.mark_running(job_id)
    assert jobs.dedup_running(conn, "collect") == job_id

    other = jobs.create_job(conn, "generate", {})
    jobs.mark_running(other)
    assert jobs.dedup_running(conn, "collect") == job_id  # scoped by type

    jobs.finish(job_id, "done")
    assert jobs.dedup_running(conn, "collect") is None  # finished no longer dedups


def test_dedup_running_skips_stale(conn):
    job_id = jobs.create_job(conn, "collect", {})
    jobs.mark_running(job_id)

    conn.execute(
        "UPDATE jobs SET heartbeat_at = datetime('now', '-6 minutes') WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    assert jobs.dedup_running(conn, "collect") is None

    conn.execute(
        "UPDATE jobs SET heartbeat_at = NULL, "
        "created_at = datetime('now', '-10 minutes') WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    assert jobs.dedup_running(conn, "collect") is None

    conn.execute(
        "UPDATE jobs SET heartbeat_at = NULL, created_at = datetime('now') "
        "WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    assert jobs.dedup_running(conn, "collect") == job_id

    conn.execute(
        "UPDATE jobs SET heartbeat_at = datetime('now') WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    assert jobs.dedup_running(conn, "collect") == job_id


def test_create_job_invalid_type_raises(conn):
    with pytest.raises(AppError):
        jobs.create_job(conn, "nope", {})


def test_run_collect_job_failure_marks_failed(conn, tmp_path, monkeypatch):
    job_id = jobs.create_job(conn, "collect", {})
    jobs.mark_running(job_id)

    def boom(conn, enabled_only=False):
        raise RuntimeError("sources exploded")

    monkeypatch.setattr("idea_hub.models.list_sources", boom)
    jobs.run_collect_job(job_id, {}, str(tmp_path / "test.db"))

    row = _get(conn, job_id)
    assert row["status"] == "failed"
    assert row["error"] == "sources exploded"
    notif = conn.execute(
        "SELECT type, level FROM notifications WHERE type = 'job_failed'"
    ).fetchone()
    assert notif is not None
    assert notif["level"] == "error"


def test_run_collect_job_connect_failure_does_not_double_raise(
    conn, tmp_path, monkeypatch
):
    job_id = jobs.create_job(conn, "collect", {})
    jobs.mark_running(job_id)
    real = db.connect
    calls = 0

    def flaky(path):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("disk full")
        return real(path)

    monkeypatch.setattr("idea_hub.db.connect", flaky)
    jobs.run_collect_job(job_id, {}, str(tmp_path / "test.db"))

    assert _get(conn, job_id)["status"] == "failed"
    assert _get(conn, job_id)["error"] == "disk full"
