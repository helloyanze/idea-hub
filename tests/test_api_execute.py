"""Task S6.2: execute job — run_execute_job 批量/预算/部分成功 + POST /api/v1/execute。

Covers: 端点（auth 401 / 无 key 400 / 空 task_ids 400 / 非法 id 409 + invalid_task_ids /
创建 job + 启动 / dedup running）、run_execute_job（批量成功、部分成功 failed_items、
全失败 failed、预算启动即超限、执行中达预算停止未开始任务不动、conflict 计入 failed_items、
progress/heartbeat 序列、幂等跳过计数、jobs.token_used 实时累加）。
"""
import json

from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.config import Config
from idea_hub.main import create_app
from idea_hub.services import executor, jobs

AUTH = ("admin", "secret")


def seed_task(conn, *, status="todo", title="任务A"):
    cur = conn.execute(
        "INSERT INTO tasks (title, idea_summary, content_type, status, "
        "feasibility_score, score_breakdown, target_desc) "
        "VALUES (?, '摘要', 'article', ?, 9, '{}', '热点')",
        (title, status),
    )
    conn.commit()
    return cur.lastrowid


def set_budget(conn, value):
    conn.execute(
        "UPDATE settings SET value = ? WHERE key = 'daily_budget_tokens'",
        (str(value),),
    )
    conn.commit()


def seed_today_usage(conn, tokens):
    """插入一条今日已消耗 tokens 的旧 job（created_at 默认今天）。"""
    job_id = jobs.create_job(conn, "collect", {})
    conn.execute(
        "UPDATE jobs SET status='done', token_used=? WHERE id = ?",
        (tokens, job_id),
    )
    conn.commit()
    return job_id


def make_execute_one(results):
    """results: {task_id: {"token_used": int, "ok": bool, "conflict": bool, "error": str}}"""
    calls = []

    def fake(conn, task_id, api_key, heartbeat=None, base_path=None):
        calls.append(task_id)
        r = results.get(task_id, {})
        return executor.ExecuteResult(
            ok=r.get("ok", True),
            token_used=r.get("token_used", 0),
            error=r.get("error"),
            conflict=r.get("conflict", False),
            saved_output=r.get("saved_output", True),
        )

    fake.calls = calls
    return fake


def make_config(tmp_path, *, auth_user="admin", auth_pass="secret",
                deepseek_api_key="sk-test"):
    return Config(
        host="127.0.0.1", port=8000,
        db_path=str(tmp_path / "test.db"),
        base_path=str(tmp_path),
        auth_user=auth_user, auth_pass=auth_pass,
        deepseek_api_key=deepseek_api_key,
        rate_limit_per_min=60, log_level="INFO",
    )


def client_for(config):
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    conn.close()
    return TestClient(create_app(config))


# ---- run_execute_job ----

def test_run_execute_job_batch_success(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    t2 = seed_task(conn, title="任务B")
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1, t2]})
    jobs.mark_running(job_id)
    fake = make_execute_one({t1: {"token_used": 120}, t2: {"token_used": 80}})
    monkeypatch.setattr(executor, "execute_one", fake)

    jobs.run_execute_job(job_id, {"task_ids": [t1, t2]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    assert fake.calls == [t1, t2]
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job["status"] == "done"
    assert job["progress"] == 100
    assert job["token_used"] == 200  # 实时累加
    result = json.loads(job["result_ref"])
    assert result == {"task_ids": [t1, t2], "failed_items": []}
    notif = conn.execute(
        "SELECT type, level, entity_id FROM notifications WHERE type = 'execute_done'"
    ).fetchone()
    assert notif is not None
    assert notif["level"] == "info"
    assert notif["entity_id"] == job_id


def test_run_execute_job_partial_success(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    t2 = seed_task(conn, title="任务B")
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1, t2]})
    jobs.mark_running(job_id)
    fake = make_execute_one({
        t1: {"token_used": 120},
        t2: {"ok": False, "error": "LLM down", "token_used": 0},
    })
    monkeypatch.setattr(executor, "execute_one", fake)

    jobs.run_execute_job(job_id, {"task_ids": [t1, t2]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    job = conn.execute("SELECT status, result_ref FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
    assert job["status"] == "done"  # 部分成功
    result = json.loads(job["result_ref"])
    assert result["task_ids"] == [t1, t2]
    assert result["failed_items"] == [
        {"task_id": t2, "error": "LLM down", "conflict": False},
    ]
    notif = conn.execute(
        "SELECT level FROM notifications WHERE type = 'execute_done'"
    ).fetchone()
    assert notif is not None
    assert notif["level"] == "warn"  # 部分失败 → warn


def test_run_execute_job_all_failed(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    t2 = seed_task(conn, title="任务B")
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1, t2]})
    jobs.mark_running(job_id)
    fake = make_execute_one({
        t1: {"ok": False, "error": "boom"},
        t2: {"ok": False, "error": "boom2"},
    })
    monkeypatch.setattr(executor, "execute_one", fake)

    jobs.run_execute_job(job_id, {"task_ids": [t1, t2]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    job = conn.execute("SELECT status, error FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert "failed" in job["error"]
    notif = conn.execute(
        "SELECT type, level FROM notifications WHERE type = 'job_failed'"
    ).fetchone()
    assert notif is not None
    assert notif["level"] == "error"


def test_run_execute_job_conflict_in_failed_items(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    t2 = seed_task(conn, title="任务B")
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1, t2]})
    jobs.mark_running(job_id)
    fake = make_execute_one({
        t1: {"token_used": 100},
        t2: {"token_used": 50, "conflict": True, "saved_output": True},
    })
    monkeypatch.setattr(executor, "execute_one", fake)

    jobs.run_execute_job(job_id, {"task_ids": [t1, t2]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    job = conn.execute("SELECT status, result_ref, token_used FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
    assert job["status"] == "done"
    assert job["token_used"] == 150  # conflict 任务的 token 也累加
    result = json.loads(job["result_ref"])
    assert result["failed_items"] == [
        {"task_id": t2, "error": result["failed_items"][0]["error"],
         "conflict": True},
    ]
    notif = conn.execute(
        "SELECT level FROM notifications WHERE type = 'execute_done'"
    ).fetchone()
    assert notif is not None and notif["level"] == "warn"


def test_run_execute_job_idempotent_skip_counts_as_success(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    t2 = seed_task(conn, title="任务B")
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1, t2]})
    jobs.mark_running(job_id)
    fake = make_execute_one({
        t1: {"token_used": 120},
        t2: {"token_used": 0, "saved_output": False},  # done+产物 跳过
    })
    monkeypatch.setattr(executor, "execute_one", fake)

    jobs.run_execute_job(job_id, {"task_ids": [t1, t2]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    job = conn.execute("SELECT status, token_used FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
    assert job["status"] == "done"
    assert job["token_used"] == 120  # 跳过任务不计 token
    notif = conn.execute(
        "SELECT level FROM notifications WHERE type = 'execute_done'"
    ).fetchone()
    assert notif is not None and notif["level"] == "info"  # 跳过不算失败


def test_run_execute_job_budget_exceeded_at_start(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    seed_today_usage(conn, 100)  # 今日已用 100
    set_budget(conn, 50)
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1]})
    jobs.mark_running(job_id)
    fake = make_execute_one({t1: {"token_used": 10}})
    monkeypatch.setattr(executor, "execute_one", fake)

    jobs.run_execute_job(job_id, {"task_ids": [t1]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    assert fake.calls == []  # 一个任务都不执行
    job = conn.execute("SELECT status, error FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert "budget" in job["error"].lower()
    notif = conn.execute(
        "SELECT type, level FROM notifications WHERE type = 'budget_exceeded'"
    ).fetchone()
    assert notif is not None and notif["level"] == "warn"


def test_run_execute_job_budget_stops_mid_run(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    t2 = seed_task(conn, title="任务B")
    t3 = seed_task(conn, title="任务C")
    seed_today_usage(conn, 30)  # 今日已用 30
    set_budget(conn, 50)
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1, t2, t3]})
    jobs.mark_running(job_id)
    fake = make_execute_one({
        t1: {"token_used": 20},  # 30+20=50 未超
        t2: {"token_used": 20},  # 50+20=70 超限 → 停止
        t3: {"token_used": 20},  # 不执行
    })
    monkeypatch.setattr(executor, "execute_one", fake)

    jobs.run_execute_job(job_id, {"task_ids": [t1, t2, t3]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    assert fake.calls == [t1, t2]  # t3 未执行
    job = conn.execute("SELECT status, error, token_used FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert "budget" in job["error"].lower()
    assert job["token_used"] == 40  # 本 job 已累加 t1+t2
    row = conn.execute("SELECT status FROM tasks WHERE id = ?", (t3,)).fetchone()
    assert row["status"] == "todo"  # 未开始任务保持原状态
    notif = conn.execute(
        "SELECT type FROM notifications WHERE type = 'budget_exceeded'"
    ).fetchone()
    assert notif is not None


def test_run_execute_job_progress_and_heartbeat(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    t2 = seed_task(conn, title="任务B")
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1, t2]})
    jobs.mark_running(job_id)
    monkeypatch.setattr(executor, "execute_one", make_execute_one({}))
    progress_calls = []
    monkeypatch.setattr(jobs, "update_progress",
                        lambda jid, pct: progress_calls.append(pct) or 1)
    heartbeats = []
    monkeypatch.setattr(jobs, "heartbeat", lambda jid: heartbeats.append(jid) or 1)

    jobs.run_execute_job(job_id, {"task_ids": [t1, t2]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    assert progress_calls == [50, 100, 100]  # 每任务后 + 完成强制 100
    assert len(heartbeats) >= 2


def test_run_execute_job_unexpected_exception_fails_job(conn, tmp_path, monkeypatch):
    t1 = seed_task(conn)
    job_id = jobs.create_job(conn, "execute", {"task_ids": [t1]})
    jobs.mark_running(job_id)

    def boom(conn, task_id, api_key, heartbeat=None, base_path=None):
        raise RuntimeError("executor crashed")

    monkeypatch.setattr(executor, "execute_one", boom)
    jobs.run_execute_job(job_id, {"task_ids": [t1]},
                         str(tmp_path / "test.db"), "sk-test",
                         base_path=str(tmp_path))

    job = conn.execute("SELECT status, error FROM jobs WHERE id = ?",
                       (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert "executor crashed" in job["error"]
    notif = conn.execute(
        "SELECT type, level FROM notifications WHERE type = 'job_failed'"
    ).fetchone()
    assert notif is not None and notif["level"] == "error"


# ---- POST /api/v1/execute 端点 ----

def test_execute_endpoint_requires_auth(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.post("/api/v1/execute", json={"task_ids": [1]}).status_code == 401


def test_execute_endpoint_no_api_key_400(tmp_path):
    config = make_config(tmp_path, deepseek_api_key="")
    client = client_for(config)
    resp = client.post("/api/v1/execute", json={"task_ids": [1]}, auth=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    assert "API_KEY" in resp.json()["error"]["message"] or "key" in resp.json()["error"]["message"]
    conn = db.connect(config.db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE type = 'execute'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_execute_endpoint_rejects_empty_task_ids(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    resp = client.post("/api/v1/execute", json={"task_ids": []}, auth=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    conn = db.connect(config.db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE type = 'execute'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_execute_endpoint_rejects_missing_task_ids_422(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post("/api/v1/execute", json={}, auth=AUTH)
    assert resp.status_code == 422


def test_execute_endpoint_invalid_task_ids_409(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    conn = db.connect(config.db_path)
    in_progress_id = seed_task(conn, status="in_progress")
    conn.close()

    resp = client.post("/api/v1/execute",
                       json={"task_ids": [in_progress_id, 99999]}, auth=AUTH)

    assert resp.status_code == 409
    body = resp.json()
    assert body["invalid_task_ids"] == [in_progress_id, 99999]  # 顺序保留
    conn = db.connect(config.db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE type = 'execute'"
        ).fetchone()[0] == 0  # 校验失败不创建 job
    finally:
        conn.close()


def test_execute_endpoint_creates_job(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = client_for(config)
    conn = db.connect(config.db_path)
    t1 = seed_task(conn)
    t2 = seed_task(conn, title="任务B")
    conn.close()
    launched = []
    monkeypatch.setattr(
        "idea_hub.routers.pipeline.jobs_service.run_execute_job",
        lambda *a, **k: launched.append(a),
    )

    resp = client.post("/api/v1/execute", json={"task_ids": [t1, t2]}, auth=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["reused"] is False
    job_id = data["job_id"]

    conn = db.connect(config.db_path)
    try:
        row = conn.execute("SELECT type, status FROM jobs WHERE id = ?",
                           (job_id,)).fetchone()
        assert row["type"] == "execute"
        assert row["status"] == "running"
    finally:
        conn.close()
    assert launched == [(job_id, {"task_ids": [t1, t2]},
                         config.db_path, "sk-test", config.base_path)]


def test_execute_endpoint_dedup_running(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = client_for(config)
    conn = db.connect(config.db_path)
    t1 = seed_task(conn)
    existing = jobs.create_job(conn, "execute", {"task_ids": [t1]})
    jobs.mark_running(existing)
    conn.close()
    launched = []
    monkeypatch.setattr(
        "idea_hub.routers.pipeline.jobs_service.run_execute_job",
        lambda *a, **k: launched.append(a),
    )

    resp = client.post("/api/v1/execute", json={"task_ids": [t1]}, auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"job_id": existing, "reused": True}
    assert launched == []  # 不重复启动
