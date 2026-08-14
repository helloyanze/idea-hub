"""Task S4.3: generate job + 任务创建 + tags 服务（services/tags.py + services/tasks.py + run_generate_job + POST /api/v1/generate）。

Covers: create_from_generation 字段继承（score_breakdown/feasibility_score/expire_at/status=todo）、
task_links 关联、idea.md 落盘（outputs/tasks/<id>/idea.md）、tags upsert（新标签创建+8 色调色板轮转、
已存在复用、去重）、run_generate_job 生命周期（progress/heartbeat/token_used/result_ref/通知）、
LLM 失败 → failed + job_failed、无候选 → done(0)、生成数少于候选（zip 部分成功）、
端点层：无 api_key 400（生成不能降级）、dedup running generate job、job 创建、auth 401。
"""
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.config import Config
from idea_hub.main import create_app
from idea_hub.services import generate, jobs, tags, tasks

AUTH = ("admin", "secret")

GENS = [
    {"title": "构思A", "idea_summary": "摘要A", "full_idea": "# 正文A\n内容A",
     "content_type": "article", "tags": ["AI", "科技"]},
    {"title": "构思B", "idea_summary": "摘要B", "full_idea": "正文B",
     "content_type": "video_script", "tags": ["科技", "教程"]},
]


def _ts(hours_ago=1):
    """相对 now 的 UTC 时间字符串（与 sqlite datetime('now') 同格式）。"""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _expire(collected_at, ttl_hours):
    return (
        datetime.strptime(collected_at, "%Y-%m-%d %H:%M:%S") + timedelta(hours=ttl_hours)
    ).strftime("%Y-%m-%d %H:%M:%S")


def seed_hotspot(conn, *, title, final_score=9, score_breakdown=None, verdict="admit",
                 ttl_hours=24, collected_at=None):
    """插入一个来源 + 热点，返回 hot_item id。"""
    cur = conn.execute(
        "INSERT INTO sources (type, name, url, ttl_hours) VALUES ('rss', ?, ?, ?)",
        ("源-" + title, "http://example.com/src/" + title, ttl_hours),
    )
    source_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO hot_items (source_id, title, url, final_score, score_breakdown, "
        "verdict, collected_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (source_id, title, "http://example.com/h/" + title, final_score,
         json.dumps(score_breakdown or {"facts": final_score or 9}, ensure_ascii=False),
         verdict, collected_at or _ts(hours_ago=1)),
    )
    conn.commit()
    return cur.lastrowid


def _make_generate_one(gens):
    """返回注入 token_usage 的 generate_one mock。"""
    def fake_generate_one(candidates, api_key, timeout=90, max_retries=2,
                          heartbeat=None, token_usage=None):
        token_usage["total"] = 120
        return gens
    return fake_generate_one


# ---- services/tasks.py: create_from_generation ----

def test_create_from_generation_inherits_hotspot_fields(conn, tmp_path):
    hotspot_id = seed_hotspot(conn, title="热点原文标题", final_score=9,
                              score_breakdown={"facts": 9, "value": 8},
                              ttl_hours=24, collected_at="2026-08-14 10:00:00")
    candidate = {
        "hotspot_id": hotspot_id, "title": "热点原文标题", "url": "http://example.com/h",
        "source_id": 7, "collected_at": "2026-08-14 10:00:00", "ttl_hours": 24,
        "final_score": 9, "score_breakdown": {"facts": 9, "value": 8},
    }
    gen = GENS[0]
    tid = tasks.create_from_generation(conn, gen, candidate, base_path=str(tmp_path))

    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert row["status"] == "todo"  # 候选已筛选，生成阶段不二次分流
    assert row["title"] == "构思A"  # 生成标题
    assert row["target_desc"] == "热点原文标题"  # 原文标题
    assert row["idea_summary"] == "摘要A"
    assert row["content_type"] == "article"
    assert row["feasibility_score"] == 9  # 继承热点 final_score
    assert json.loads(row["score_breakdown"]) == {"facts": 9, "value": 8}  # 继承热点 JSON
    assert row["expire_at"] == "2026-08-15 10:00:00"  # collected_at + ttl_hours
    assert "T" not in row["expire_at"]
    assert re.fullmatch(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", row["expire_at"]
    )

    link = conn.execute(
        "SELECT hot_item_id FROM task_links WHERE task_id = ?", (tid,)
    ).fetchone()
    assert link["hot_item_id"] == hotspot_id

    idea_path = tmp_path / "outputs" / "tasks" / str(tid) / "idea.md"
    assert idea_path.is_file()
    assert idea_path.read_text(encoding="utf-8") == "# 正文A\n内容A"

    tag_rows = conn.execute(
        "SELECT t.name, t.color FROM task_tags tt JOIN tags t ON t.id = tt.tag_id "
        "WHERE tt.task_id = ? ORDER BY t.id", (tid,)
    ).fetchall()
    assert [(r["name"], r["color"]) for r in tag_rows] == [
        ("AI", "#3b82f6"), ("科技", "#ef4444"),
    ]


def test_create_from_generation_no_ttl_no_expire(conn, tmp_path):
    hotspot_id = seed_hotspot(conn, title="无时效热点", final_score=8,
                              score_breakdown={}, ttl_hours=None,
                              collected_at="2026-08-01 10:00:00")
    candidate = {
        "hotspot_id": hotspot_id, "title": "无时效热点", "url": "http://example.com/h2",
        "source_id": 8, "collected_at": "2026-08-01 10:00:00", "ttl_hours": None,
        "final_score": 8, "score_breakdown": {},
    }
    tid = tasks.create_from_generation(conn, GENS[1], candidate, base_path=str(tmp_path))
    row = conn.execute("SELECT expire_at, feasibility_score FROM tasks WHERE id = ?", (tid,)).fetchone()
    assert row["expire_at"] is None  # 热点无 ttl → NULL（不过期）
    assert row["feasibility_score"] == 8
    assert (tmp_path / "outputs" / "tasks" / str(tid) / "idea.md").is_file()


# ---- services/tags.py: upsert_by_names ----

def test_tags_upsert_creates_with_rotating_colors(conn):
    ids = tags.upsert_by_names(conn, ["AI", "科技"])
    conn.commit()
    rows = conn.execute("SELECT name, color FROM tags ORDER BY id").fetchall()
    assert [(r["name"], r["color"]) for r in rows] == [
        ("AI", "#3b82f6"),  # 空表：0 % 8
        ("科技", "#ef4444"),  # 1 % 8
    ]
    assert len(ids) == 2


def test_tags_upsert_reuses_existing_and_dedupes(conn):
    first = tags.upsert_by_names(conn, ["AI"])
    conn.commit()
    second = tags.upsert_by_names(conn, ["AI", "AI", " 科技 "])
    conn.commit()
    assert second[0] == first[0]  # 已存在复用同一 id
    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 2  # 去重后仅 2 个
    assert conn.execute("SELECT name FROM tags WHERE id = ?", (second[1],)).fetchone()[0] == "科技"


def test_tags_upsert_rotates_after_existing_tags(conn):
    for i in range(8):
        conn.execute("INSERT INTO tags (name, color) VALUES (?, ?)", (f"tag{i}", "#000000"))
    conn.commit()
    ids = tags.upsert_by_names(conn, ["第九个", "第十个"])
    conn.commit()
    colors = {
        r["name"]: r["color"]
        for r in conn.execute("SELECT name, color FROM tags WHERE name IN ('第九个','第十个')")
    }
    assert colors == {"第九个": "#3b82f6", "第十个": "#ef4444"}  # 8 % 8 = 0, 9 % 8 = 1


def test_tags_upsert_empty_and_blank(conn):
    assert tags.upsert_by_names(conn, []) == []
    assert tags.upsert_by_names(conn, ["", "  ", None]) == []
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0


# ---- run_generate_job：生命周期 ----

def test_run_generate_job_creates_tasks(conn, tmp_path, monkeypatch):
    collected = _ts(hours_ago=1)
    h1 = seed_hotspot(conn, title="热点A", final_score=9,
                      score_breakdown={"facts": 9}, ttl_hours=24, collected_at=collected)
    h2 = seed_hotspot(conn, title="热点B", final_score=8,
                      score_breakdown={"facts": 8}, ttl_hours=24)
    expected_expire = _expire(collected, 24)
    job_id = jobs.create_job(conn, "generate", {})
    jobs.mark_running(job_id)
    monkeypatch.setattr(generate, "generate_one", _make_generate_one(GENS))

    jobs.run_generate_job(job_id, {}, str(tmp_path / "test.db"), "sk-test",
                          base_path=str(tmp_path))

    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert job["status"] == "done"
    assert job["progress"] == 100
    assert job["token_used"] == 120  # generate job 记录 token
    result = json.loads(job["result_ref"])
    assert result["task_count"] == 2
    assert len(result["task_ids"]) == 2

    rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    assert len(rows) == 2
    first, second = rows
    assert first["title"] == "构思A"
    assert first["target_desc"] == "热点A"  # final_score 9 在前
    assert first["feasibility_score"] == 9
    assert json.loads(first["score_breakdown"]) == {"facts": 9}
    assert first["expire_at"] == expected_expire
    assert second["title"] == "构思B"
    assert second["target_desc"] == "热点B"
    assert second["feasibility_score"] == 8

    links = conn.execute("SELECT task_id, hot_item_id FROM task_links ORDER BY task_id").fetchall()
    assert {r["hot_item_id"] for r in links} == {h1, h2}

    for row in rows:
        assert (tmp_path / "outputs" / "tasks" / str(row["id"]) / "idea.md").is_file()

    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 3  # AI/科技/教程

    notif = conn.execute(
        "SELECT type, level, entity_id, body FROM notifications WHERE type = 'generate_done'"
    ).fetchone()
    assert notif is not None
    assert notif["level"] == "info"
    assert notif["entity_id"] == job_id


def test_expire_at_inherited(conn, tmp_path, monkeypatch):
    collected = _ts(hours_ago=1)
    seed_hotspot(conn, title="有时效", ttl_hours=24, collected_at=collected)
    seed_hotspot(conn, title="无时效", ttl_hours=None, collected_at=_ts(hours_ago=48))
    job_id = jobs.create_job(conn, "generate", {})
    jobs.mark_running(job_id)
    monkeypatch.setattr(generate, "generate_one", _make_generate_one(GENS))

    jobs.run_generate_job(job_id, {}, str(tmp_path / "test.db"), "sk-test",
                          base_path=str(tmp_path))

    rows = conn.execute("SELECT target_desc, expire_at FROM tasks ORDER BY id").fetchall()
    by_title = {r["target_desc"]: r["expire_at"] for r in rows}
    assert by_title["有时效"] == _expire(collected, 24)
    assert by_title["无时效"] is None


def test_run_generate_job_progress_and_heartbeat(conn, tmp_path, monkeypatch):
    seed_hotspot(conn, title="热点A")
    seed_hotspot(conn, title="热点B")
    job_id = jobs.create_job(conn, "generate", {})
    jobs.mark_running(job_id)
    monkeypatch.setattr(generate, "generate_one", _make_generate_one(GENS))

    progress_calls = []
    monkeypatch.setattr(jobs, "update_progress",
                        lambda jid, pct: progress_calls.append(pct) or 1)
    heartbeats = []
    monkeypatch.setattr(jobs, "heartbeat", lambda jid: heartbeats.append(jid) or 1)

    jobs.run_generate_job(job_id, {}, str(tmp_path / "test.db"), "sk-test",
                          base_path=str(tmp_path))

    assert progress_calls == [50, 100, 100]  # 每候选后更新，完成后强制 100
    assert len(heartbeats) >= 2  # 每候选后 heartbeat（LLM 尝试前心跳由 chat_json 注入）


def test_run_generate_job_failure_marks_failed(conn, tmp_path, monkeypatch):
    seed_hotspot(conn, title="热点A")
    job_id = jobs.create_job(conn, "generate", {})
    jobs.mark_running(job_id)

    def boom(candidates, api_key, **kwargs):
        raise ValueError("LLM down")

    monkeypatch.setattr(generate, "generate_one", boom)
    jobs.run_generate_job(job_id, {}, str(tmp_path / "test.db"), "sk-test",
                          base_path=str(tmp_path))

    row = conn.execute("SELECT status, error FROM jobs WHERE id = ?", (job_id,)).fetchone()
    assert row["status"] == "failed"
    assert "LLM down" in row["error"]
    notif = conn.execute(
        "SELECT type, level FROM notifications WHERE type = 'job_failed'"
    ).fetchone()
    assert notif is not None
    assert notif["level"] == "error"


def test_run_generate_job_no_candidates(conn, tmp_path, monkeypatch):
    seed_hotspot(conn, title="低分热点", final_score=5)  # 低于阈值，不是候选
    job_id = jobs.create_job(conn, "generate", {})
    jobs.mark_running(job_id)
    called = []
    monkeypatch.setattr(generate, "generate_one",
                        lambda *a, **k: called.append(1) or [])

    jobs.run_generate_job(job_id, {}, str(tmp_path / "test.db"), "sk-test",
                          base_path=str(tmp_path))

    assert called == []  # 无候选不调 LLM
    job = conn.execute(
        "SELECT status, progress, result_ref FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["status"] == "done"
    assert job["progress"] == 100
    assert json.loads(job["result_ref"]) == {
        "task_ids": [], "task_count": 0, "dropped": 0
    }
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0
    notif = conn.execute(
        "SELECT type FROM notifications WHERE type = 'generate_done'"
    ).fetchone()
    assert notif is not None


def test_run_generate_job_fewer_gens_than_candidates(conn, tmp_path, monkeypatch):
    seed_hotspot(conn, title="热点A")
    seed_hotspot(conn, title="热点B")
    job_id = jobs.create_job(conn, "generate", {})
    jobs.mark_running(job_id)
    monkeypatch.setattr(generate, "generate_one", _make_generate_one(GENS[:1]))  # LLM 少返回

    jobs.run_generate_job(job_id, {}, str(tmp_path / "test.db"), "sk-test",
                          base_path=str(tmp_path))

    job = conn.execute(
        "SELECT status, progress, result_ref FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["status"] == "done"
    assert job["progress"] == 100
    result = json.loads(job["result_ref"])
    assert result["task_count"] == 1
    assert result["dropped"] == 1
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_run_generate_job_zero_gens_with_candidates(conn, tmp_path, monkeypatch):
    seed_hotspot(conn, title="热点A")
    seed_hotspot(conn, title="热点B")
    job_id = jobs.create_job(conn, "generate", {})
    jobs.mark_running(job_id)
    monkeypatch.setattr(generate, "generate_one", _make_generate_one([]))

    jobs.run_generate_job(job_id, {}, str(tmp_path / "test.db"), "sk-test",
                          base_path=str(tmp_path))

    job = conn.execute(
        "SELECT status, progress, result_ref FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["status"] == "done"
    assert job["progress"] == 100
    result = json.loads(job["result_ref"])
    assert result["task_count"] == 0
    assert result["dropped"] == 2
    notif = conn.execute(
        "SELECT level FROM notifications WHERE type = 'generate_done'"
    ).fetchone()
    assert notif is not None
    assert notif["level"] == "warn"


# ---- generate_one token_usage 透传 ----

def test_generate_one_reports_token_usage(monkeypatch):
    raw = [{"title": "x", "content_type": "article", "tags": []}]

    def fake_chat_json(messages, api_key, timeout=90, max_retries=2,
                       heartbeat=None, token_usage=None):
        token_usage["total"] = 42
        return raw

    monkeypatch.setattr(generate, "chat_json", fake_chat_json)
    usage = {}
    out = generate.generate_one(
        [{"hotspot_id": 1, "title": "t", "collected_at": "2026-08-14 10:00:00",
          "ttl_hours": None, "final_score": 9, "score_breakdown": {}}],
        api_key="sk-test", token_usage=usage,
    )
    assert usage["total"] == 42
    assert out[0]["title"] == "x"


# ---- POST /api/v1/generate 端点 ----

def make_config(tmp_path, *, auth_user="admin", auth_pass="secret", deepseek_api_key=""):
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


def test_generate_endpoint_requires_auth(tmp_path):
    client = client_for(make_config(tmp_path, deepseek_api_key="sk-test"))
    assert client.post("/api/v1/generate", json={}).status_code == 401


def test_generate_endpoint_no_api_key_400(tmp_path):
    config = make_config(tmp_path)  # 无 key
    client = client_for(config)
    resp = client.post("/api/v1/generate", json={}, auth=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    assert "API_KEY" in resp.json()["error"]["message"] or "key" in resp.json()["error"]["message"]
    conn = db.connect(config.db_path)
    assert conn.execute("SELECT COUNT(*) FROM jobs WHERE type = 'generate'").fetchone()[0] == 0
    conn.close()


def test_generate_endpoint_rejects_zero_count_without_creating_job(tmp_path):
    config = make_config(tmp_path, deepseek_api_key="sk-test")
    client = client_for(config)

    resp = client.post("/api/v1/generate", auth=AUTH, json={"count": 0})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    conn = db.connect(config.db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE type = 'generate'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_generate_endpoint_rejects_negative_count_without_creating_job(tmp_path):
    config = make_config(tmp_path, deepseek_api_key="sk-test")
    client = client_for(config)

    resp = client.post("/api/v1/generate", auth=AUTH, json={"count": -1})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    conn = db.connect(config.db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE type = 'generate'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_generate_endpoint_creates_job(tmp_path, monkeypatch):
    config = make_config(tmp_path, deepseek_api_key="sk-test")
    client = client_for(config)
    launched = []
    monkeypatch.setattr(
        "idea_hub.routers.pipeline.jobs_service.run_generate_job",
        lambda *a, **k: launched.append(a),
    )

    resp = client.post("/api/v1/generate", auth=AUTH, json={"count": 3})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["reused"] is False
    job_id = data["job_id"]

    conn = db.connect(config.db_path)
    try:
        row = conn.execute("SELECT type, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        assert row["type"] == "generate"
        assert row["status"] == "running"
    finally:
        conn.close()
    assert launched == [(job_id, {"count": 3, "hotspot_ids": None},
                         config.db_path, "sk-test", config.base_path)]


def test_generate_dedup_running(tmp_path, monkeypatch):
    config = make_config(tmp_path, deepseek_api_key="sk-test")
    client = client_for(config)
    conn = db.connect(config.db_path)
    existing = jobs.create_job(conn, "generate", {"count": 1})
    jobs.mark_running(existing)
    conn.close()
    launched = []
    monkeypatch.setattr(
        "idea_hub.routers.pipeline.jobs_service.run_generate_job",
        lambda *a, **k: launched.append(a),
    )

    resp = client.post("/api/v1/generate", auth=AUTH, json={"count": 1})
    assert resp.status_code == 200
    assert resp.json()["data"] == {"job_id": existing, "reused": True}
    assert launched == []  # 不新建 job、不重复启动
