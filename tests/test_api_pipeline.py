"""Tests for the Idea Hub pipeline/jobs API (Task S2.5).

Covers: POST /api/v1/collect (async job -> hot_items written with
verdict=admit degradation, keyword filter + URL dedup wiring, per-source
progress), dedup of a running collect job (reused: true), source_ids
selection, job failure (failed + job_failed notification), GET
/api/v1/jobs/{id}, GET /api/v1/jobs with type/status/page filters, and
auth requirements.
"""
import json
import time

from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.collectors.base import RawItem
from idea_hub.collectors.rss import RssCollector
from idea_hub.config import Config
from idea_hub.main import create_app
from idea_hub.services import jobs

AUTH = ("admin", "secret")

VALID_SOURCE = {
    "type": "rss",
    "name": "测试源",
    "url": "http://example.com/feed.xml",
}


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


def _create_source(client, **overrides):
    body = dict(VALID_SOURCE)
    body.update(overrides)
    resp = client.post("/api/v1/sources", auth=AUTH, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _sample_items(n=3, keyword="AI"):
    return [
        RawItem(
            title=f"{keyword} 条目{i}",
            url=f"http://example.com/item/{i}",
            content_snapshot=f"快照{i}",
            source_id=0,
        )
        for i in range(1, n + 1)
    ]


def _wait_job(config, job_id, statuses=("done", "failed"), timeout=5.0):
    """Poll the jobs table until the job leaves the running state."""
    deadline = time.monotonic() + timeout
    conn = db.connect(config.db_path)
    try:
        while time.monotonic() < deadline:
            row = conn.execute(
                "SELECT status FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row and row["status"] in statuses:
                return row["status"]
            time.sleep(0.02)
    finally:
        conn.close()
    raise AssertionError(f"job {job_id} did not reach {statuses} in {timeout}s")


# ---- auth ----

def test_collect_and_jobs_require_auth(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.post("/api/v1/collect", json={}).status_code == 401
    assert client.get("/api/v1/jobs").status_code == 401
    assert client.get("/api/v1/jobs/1").status_code == 401


# ---- POST /api/v1/collect ----

def test_collect_creates_hot_items(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client, keywords="AI")
    monkeypatch.setattr(RssCollector, "fetch", lambda self: _sample_items(3))

    resp = client.post("/api/v1/collect", auth=AUTH, json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["reused"] is False
    job_id = data["job_id"]

    assert _wait_job(config, job_id) == "done"

    conn = db.connect(config.db_path)
    try:
        rows = conn.execute("SELECT * FROM hot_items ORDER BY id").fetchall()
        assert len(rows) == 3
        assert all(r["verdict"] == "admit" for r in rows)
        assert all(r["source_id"] == source["id"] for r in rows)

        job = conn.execute(
            "SELECT status, progress, result_ref, error FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        assert job["status"] == "done"
        assert job["progress"] == 100
        assert job["error"] is None
        result = json.loads(job["result_ref"])
        assert result["hotspot_count"] == 3
        assert result["errors"] == []

        notif = conn.execute(
            "SELECT type, level, entity_id FROM notifications WHERE type = 'collect_done'"
        ).fetchone()
        assert notif is not None
        assert notif["level"] == "info"
        assert notif["entity_id"] == job_id
    finally:
        conn.close()


def test_collect_applies_keywords_filter(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = client_for(config)
    _create_source(client, keywords="AI")

    def fetch(self):
        items = _sample_items(2)
        items.append(
            RawItem(
                title="无关条目",
                url="http://example.com/other",
                content_snapshot="x",
                source_id=0,
            )
        )
        return items

    monkeypatch.setattr(RssCollector, "fetch", fetch)
    resp = client.post("/api/v1/collect", auth=AUTH, json={})
    job_id = resp.json()["data"]["job_id"]
    assert _wait_job(config, job_id) == "done"

    conn = db.connect(config.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM hot_items").fetchone()[0]
        assert count == 2
    finally:
        conn.close()


def test_collect_dedups_existing_urls(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = client_for(config)
    _create_source(client)
    monkeypatch.setattr(RssCollector, "fetch", lambda self: _sample_items(3))

    first = client.post("/api/v1/collect", auth=AUTH, json={}).json()["data"]["job_id"]
    assert _wait_job(config, first) == "done"

    second = client.post("/api/v1/collect", auth=AUTH, json={}).json()["data"]["job_id"]
    assert _wait_job(config, second) == "done"

    conn = db.connect(config.db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM hot_items").fetchone()[0]
        assert count == 3  # second run inserted nothing new
        result = json.loads(
            conn.execute("SELECT result_ref FROM jobs WHERE id = ?", (second,)).fetchone()[0]
        )
        assert result["hotspot_count"] == 0
    finally:
        conn.close()


def test_collect_dedup_returns_same_job(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    # Establish an in-flight collect job directly. (Deterministic: under
    # TestClient the portal runs the fire-and-forget worker thread before
    # delivering the response, so a real second POST could never observe the
    # first job in 'running' state.)
    conn = db.connect(config.db_path)
    job_id = jobs.create_job(conn, "collect", {"source_ids": None})
    jobs.mark_running(job_id)
    conn.close()

    # A second POST while a collect job is running reuses it.
    resp = client.post("/api/v1/collect", auth=AUTH, json={})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"job_id": job_id, "reused": True}


def test_collect_with_source_ids_only_collects_selected(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = client_for(config)
    s1 = _create_source(client, name="A", url="http://a.example/feed")
    _create_source(client, name="B", url="http://b.example/feed")

    fetched = []

    def fetch(self):
        fetched.append(self.source_id)
        return [
            RawItem(
                title=f"T{self.source_id}",
                url=f"http://example.com/t{self.source_id}",
                content_snapshot="s",
                source_id=0,
            )
        ]

    monkeypatch.setattr(RssCollector, "fetch", fetch)
    resp = client.post(
        "/api/v1/collect", auth=AUTH, json={"source_ids": [s1["id"]]}
    )
    job_id = resp.json()["data"]["job_id"]
    assert _wait_job(config, job_id) == "done"

    assert fetched == [s1["id"]]
    conn = db.connect(config.db_path)
    try:
        rows = conn.execute("SELECT source_id, title FROM hot_items").fetchall()
        assert [(r["source_id"], r["title"]) for r in rows] == [
            (s1["id"], f"T{s1['id']}")
        ]
    finally:
        conn.close()


def test_collect_job_failure_marks_failed_and_notifies(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = client_for(config)

    def boom(conn, enabled_only=False):
        raise RuntimeError("boom: sources crashed")

    monkeypatch.setattr("idea_hub.models.list_sources", boom)
    resp = client.post("/api/v1/collect", auth=AUTH, json={})
    assert resp.status_code == 200
    job_id = resp.json()["data"]["job_id"]
    assert _wait_job(config, job_id) == "failed"

    conn = db.connect(config.db_path)
    try:
        error = conn.execute("SELECT error FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
        assert error == "boom: sources crashed"
        notif = conn.execute(
            "SELECT type, level FROM notifications WHERE type = 'job_failed'"
        ).fetchone()
        assert notif is not None
        assert notif["level"] == "error"
    finally:
        conn.close()


# ---- GET /api/v1/jobs ----

def test_get_job_returns_status_and_result(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    conn = db.connect(config.db_path)
    job_id = jobs.create_job(conn, "collect", {})
    jobs.mark_running(job_id)
    jobs.finish(job_id, "done", result_ref='{"hotspot_count": 5}')
    conn.close()

    resp = client.get(f"/api/v1/jobs/{job_id}", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == job_id
    assert data["type"] == "collect"
    assert data["status"] == "done"
    assert data["progress"] == 0
    assert data["result_ref"] == '{"hotspot_count": 5}'
    assert data["error"] is None


def test_get_job_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/jobs/999", auth=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_list_jobs_filters(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    conn = db.connect(config.db_path)
    for _ in range(3):
        job_id = jobs.create_job(conn, "collect", {})
        jobs.mark_running(job_id)
        jobs.finish(job_id, "done")
    gen_id = jobs.create_job(conn, "generate", {})
    jobs.mark_running(gen_id)
    conn.close()

    resp = client.get("/api/v1/jobs", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 4
    assert len(data["items"]) == 4

    resp = client.get("/api/v1/jobs", auth=AUTH, params={"type": "collect"})
    assert resp.json()["data"]["total"] == 3

    resp = client.get("/api/v1/jobs", auth=AUTH, params={"status": "done"})
    assert resp.json()["data"]["total"] == 3

    resp = client.get(
        "/api/v1/jobs", auth=AUTH, params={"type": "collect", "status": "running"}
    )
    assert resp.json()["data"]["total"] == 0


def test_list_jobs_page_param(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    conn = db.connect(config.db_path)
    for _ in range(25):
        job_id = jobs.create_job(conn, "collect", {})
        jobs.mark_running(job_id)
        jobs.finish(job_id, "done")
    conn.close()

    resp = client.get("/api/v1/jobs", auth=AUTH, params={"page": 2})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["page"] == 2
    assert data["total"] == 25
    assert len(data["items"]) == 5

    resp = client.get("/api/v1/jobs", auth=AUTH, params={"page": 0})
    assert resp.status_code == 400
