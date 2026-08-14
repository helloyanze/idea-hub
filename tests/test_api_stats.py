"""Task S7.2: stats API tests.

Covers: GET /api/v1/stats (queue counts by status, hotspot admit/discard
counts, split token accounting — execution from tasks.token_used only,
generation from jobs.token_used WHERE type IN (collect, generate) — today
produced done tasks, active jobs, scheduler last_tick), GET
/api/v1/stats/trends (daily hotspots/tasks/outputs for N days, zero-filled),
auth 401. Contract: unified {data, error} responses.
"""
from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.config import Config
from idea_hub.main import create_app

AUTH = ("admin", "secret")


def make_config(tmp_path):
    return Config(
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


def client_for(config):
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    conn.close()
    return TestClient(create_app(config))


def _exec(config, sql, params=()):
    conn = db.connect(config.db_path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _seed_source(config):
    _exec(config, "INSERT INTO sources (type, name, url) VALUES ('rss', '源', 'http://a/feed')")
    return 1


def _seed_all(config):
    """Create a representative data set for the stats assertions."""
    _seed_source(config)
    # hotspots: 3 admit + 2 discard
    for i in range(3):
        _exec(config, "INSERT INTO hot_items (source_id, title, verdict, url) "
                      "VALUES (1, ?, 'admit', ?)", (f"admit{i}", f"http://a/admit{i}"))
    for i in range(2):
        _exec(config, "INSERT INTO hot_items (source_id, title, verdict, url) "
                      "VALUES (1, ?, 'discard', ?)", (f"discard{i}", f"http://a/discard{i}"))
    # tasks: 2 todo / 1 waiting / 2 in_progress / 1 done today / 1 done yesterday
    for i in range(2):
        _exec(config, "INSERT INTO tasks (title, status, token_used) VALUES (?, 'todo', 50)",
              (f"todo{i}",))
    _exec(config, "INSERT INTO tasks (title, status, token_used) VALUES ('waiting1', 'waiting', 0)")
    for i in range(2):
        _exec(config, "INSERT INTO tasks (title, status, token_used) VALUES (?, 'in_progress', 25)",
              (f"progress{i}",))
    _exec(config, "INSERT INTO tasks (title, status, token_used, completed_at) "
                  "VALUES ('done_today', 'done', 100, datetime('now'))")
    _exec(config, "INSERT INTO tasks (title, status, token_used, completed_at) "
                  "VALUES ('done_yesterday', 'done', 10, datetime('now', '-1 day'))")
    # jobs: collect 30 + generate 40 (counted), execute 60 (NOT counted in generation)
    _exec(config, "INSERT INTO jobs (type, status, token_used) VALUES ('collect', 'done', 30)")
    _exec(config, "INSERT INTO jobs (type, status, token_used) VALUES ('generate', 'done', 40)")
    _exec(config, "INSERT INTO jobs (type, status, token_used) VALUES ('execute', 'done', 60)")
    _exec(config, "INSERT INTO jobs (type, status) VALUES ('collect', 'pending')")
    _exec(config, "INSERT INTO jobs (type, status) VALUES ('generate', 'running')")
    _exec(config, "INSERT INTO jobs (type, status) VALUES ('execute', 'failed')")
    # scheduler last tick
    _exec(config, "INSERT INTO settings (key, value, value_type) "
                  "VALUES ('scheduler_last_tick', '2026-08-14 10:00:00', 'string')")


# ---- auth ----

def test_stats_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/stats").status_code == 401
    assert client.get("/api/v1/stats/trends").status_code == 401


# ---- stats ----

def test_stats_queue_counts(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_all(config)
    data = client.get("/api/v1/stats", auth=AUTH).json()["data"]
    assert data["queue"] == {"todo": 2, "waiting": 1, "in_progress": 2, "done": 2}


def test_stats_hotspots_counts(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_all(config)
    data = client.get("/api/v1/stats", auth=AUTH).json()["data"]
    assert data["hotspots"] == {"total": 5, "admit": 3, "discard": 2}


def test_stats_token_split_accounting(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_all(config)
    data = client.get("/api/v1/stats", auth=AUTH).json()["data"]
    tokens = data["tokens"]
    # execution = SUM(tasks.token_used) = 50*2 + 0 + 25*2 + 100 + 10 = 260
    assert tokens["execution_total"] == 260
    # generation = SUM(jobs.token_used) WHERE type IN (collect, generate) = 30 + 40 = 70
    # execute job's 60 tokens must NOT appear in either number
    assert tokens["generation_total"] == 70


def test_stats_today_produced(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_all(config)
    data = client.get("/api/v1/stats", auth=AUTH).json()["data"]
    # only the done task completed today counts
    assert data["today_produced"] == 1


def test_stats_active_jobs(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_all(config)
    data = client.get("/api/v1/stats", auth=AUTH).json()["data"]
    # pending(1) + running(1) = 2 active; done/failed excluded
    assert data["active_jobs"] == 2


def test_stats_scheduler_last_tick(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_all(config)
    data = client.get("/api/v1/stats", auth=AUTH).json()["data"]
    assert data["scheduler"]["last_tick"] == "2026-08-14 10:00:00"


def test_stats_scheduler_last_tick_none(tmp_path):
    client = client_for(make_config(tmp_path))
    data = client.get("/api/v1/stats", auth=AUTH).json()["data"]
    assert data["scheduler"]["last_tick"] is None


def test_stats_empty_db(tmp_path):
    client = client_for(make_config(tmp_path))
    data = client.get("/api/v1/stats", auth=AUTH).json()["data"]
    assert data["queue"] == {"todo": 0, "waiting": 0, "in_progress": 0, "done": 0}
    assert data["hotspots"] == {"total": 0, "admit": 0, "discard": 0}
    assert data["tokens"] == {"execution_total": 0, "generation_total": 0}
    assert data["today_produced"] == 0
    assert data["active_jobs"] == 0


# ---- trends ----

def test_trends_daily_counts_zero_filled(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    # hotspots 2 days ago, task 3 days ago, output 2 days ago
    _exec(config, "INSERT INTO hot_items (source_id, title, collected_date) "
                  "VALUES (1, '热点', date('now', '-2 days'))")
    _exec(config, "INSERT INTO tasks (title, created_at) "
                  "VALUES ('任务', datetime('now', '-3 days'))")
    _exec(config, "INSERT INTO tasks (title) VALUES ('任务2')")
    task_id = 2
    _exec(config, "INSERT INTO outputs (task_id, version, content, created_at) "
                  "VALUES (?, 1, '产物内容', datetime('now', '-2 days'))", (task_id,))

    data = client.get("/api/v1/stats/trends?days=7", auth=AUTH).json()["data"]
    items = data["items"]
    assert len(items) == 7
    by_date = {it["date"]: it for it in items}
    assert len(by_date) == 7  # distinct dates, zero-filled
    # find the seeded days by offset from today
    today = items[-1]["date"]
    from datetime import date, timedelta
    day_minus_2 = (date.fromisoformat(today) - timedelta(days=2)).isoformat()
    day_minus_3 = (date.fromisoformat(today) - timedelta(days=3)).isoformat()
    assert by_date[day_minus_2]["hotspots"] == 1
    assert by_date[day_minus_2]["outputs"] == 1
    assert by_date[day_minus_3]["tasks"] == 1
    assert by_date[today]["tasks"] == 1
    # sum across days matches totals
    assert sum(it["hotspots"] for it in items) == 1
    assert sum(it["tasks"] for it in items) == 2
    assert sum(it["outputs"] for it in items) == 1


def test_trends_days_param_clamped(tmp_path):
    client = client_for(make_config(tmp_path))
    data = client.get("/api/v1/stats/trends?days=3", auth=AUTH).json()["data"]
    assert len(data["items"]) == 3
    data = client.get("/api/v1/stats/trends?days=0", auth=AUTH).json()["data"]
    assert len(data["items"]) == 1
    data = client.get("/api/v1/stats/trends?days=999", auth=AUTH).json()["data"]
    assert len(data["items"]) == 90
