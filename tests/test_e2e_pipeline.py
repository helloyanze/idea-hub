"""Task S8.2: end-to-end pipeline test.

Drives the complete core chain through the FastAPI TestClient with all
external calls mocked (collector network, LLM scoring, LLM generate,
LLM execute):

  create source -> collect (mock fetch, mock scorer admit/discard) ->
  generate (mock chat_json -> task on board) -> board query ->
  execute (mock chat_text -> output.md on disk) -> output read ->
  search hit (outputs_fts trigram) -> stats correct -> notifications exist.

Also verifies S8.1 production static serving: GET / returns the built
frontend (web/dist) served by the same FastAPI app.
"""
import json
import time

from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.collectors.base import RawItem
from idea_hub.collectors.rss import RssCollector
from idea_hub.config import Config
from idea_hub.main import create_app

AUTH = ("admin", "secret")


def make_config(tmp_path):
    return Config(
        host="127.0.0.1",
        port=8000,
        db_path=str(tmp_path / "idea.db"),
        base_path=str(tmp_path),
        auth_user="admin",
        auth_pass="secret",
        deepseek_api_key="test-key",
        rate_limit_per_min=1000,
        log_level="INFO",
    )


def client_for(config, static_dir=None):
    conn = db.connect(config.db_path)
    db.init_schema(conn)
    conn.close()
    return TestClient(create_app(config, static_dir=static_dir))


def _wait_job(config, job_id, statuses=("done", "failed"), timeout=10.0):
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


def _job_result(config, job_id) -> dict:
    conn = db.connect(config.db_path)
    try:
        row = conn.execute(
            "SELECT result_ref FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["result_ref"]
    return json.loads(row["result_ref"])


def test_full_pipeline_collect_to_output(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = client_for(config)

    # 1) 建源
    resp = client.post(
        "/api/v1/sources",
        auth=AUTH,
        json={"type": "rss", "name": "e2e源", "url": "http://example.com/e2e.xml"},
    )
    assert resp.status_code == 200, resp.text
    source_id = resp.json()["data"]["id"]

    # 2) collect：mock 抓取 + mock LLM 评分（1 admit + 1 discard）
    def fake_fetch(self):
        return [
            RawItem(
                title="AI 大模型新突破",
                url="http://example.com/a1",
                content_snapshot="快照1",
                source_id=0,
            ),
            RawItem(
                title="无聊低质消息",
                url="http://example.com/a2",
                content_snapshot="快照2",
                source_id=0,
            ),
        ]

    monkeypatch.setattr(RssCollector, "fetch", fake_fetch)

    def fake_score_batch(items, api_key, dimensions, token_usage=None, **kwargs):
        if token_usage is not None:
            token_usage["total"] = 50
        return {
            0: {"facts": 9, "verification": 9, "timeliness": 9, "value": 9},
            1: {"facts": 1, "verification": 1, "timeliness": 1, "value": 1},
        }

    monkeypatch.setattr(
        "idea_hub.services.scorer._llm_score_batch", fake_score_batch
    )

    resp = client.post("/api/v1/collect", auth=AUTH, json={})
    assert resp.status_code == 200, resp.text
    collect_job = resp.json()["data"]["job_id"]
    assert _wait_job(config, collect_job) == "done"

    conn = db.connect(config.db_path)
    try:
        rows = conn.execute(
            "SELECT title, verdict, final_score, source_id FROM hot_items ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        by_title = {r["title"]: r for r in rows}
        assert by_title["AI 大模型新突破"]["verdict"] == "admit"
        assert by_title["AI 大模型新突破"]["final_score"] == 9
        assert by_title["AI 大模型新突破"]["source_id"] == source_id
        assert by_title["无聊低质消息"]["verdict"] == "discard"
        assert by_title["无聊低质消息"]["final_score"] == 1
    finally:
        conn.close()

    # 3) 预算设置：避免 execute 被每日 token 预算拦截（generate 已记账）
    resp = client.put(
        "/api/v1/settings",
        auth=AUTH,
        json={"key": "daily_budget_tokens", "value": 1000000},
    )
    assert resp.status_code == 200, resp.text

    # 4) generate：mock chat_json -> 任务入看板
    def fake_chat_json(messages, api_key, **kwargs):
        if kwargs.get("token_usage") is not None:
            kwargs["token_usage"]["total"] = 120
        return [
            {
                "title": "AI 大模型新突破深度解析",
                "idea_summary": "围绕 AI 大模型新突破写一篇深度解析文章",
                "full_idea": "# AI 大模型新突破深度解析\n\n正文草稿。",
                "content_type": "article",
                "tags": ["AI", "大模型"],
            }
        ]

    monkeypatch.setattr("idea_hub.services.generate.chat_json", fake_chat_json)

    resp = client.post("/api/v1/generate", auth=AUTH, json={"count": 1})
    assert resp.status_code == 200, resp.text
    gen_job = resp.json()["data"]["job_id"]
    assert _wait_job(config, gen_job) == "done"
    gen_result = _job_result(config, gen_job)
    assert gen_result["task_count"] == 1
    task_id = gen_result["task_ids"][0]

    # 5) 看板查询：任务在 todo
    resp = client.get("/api/v1/tasks", auth=AUTH, params={"status": "todo"})
    assert resp.status_code == 200
    board = resp.json()["data"]
    assert board["total"] == 1
    assert board["items"][0]["id"] == task_id

    # 6) execute：mock chat_text -> output.md 落盘
    def fake_chat_text(messages, api_key, **kwargs):
        if kwargs.get("token_usage") is not None:
            kwargs["token_usage"]["total"] = 200
        return "# AI 大模型新突破深度解析\n\n量子计算与人工智能融合正在加速，行业迎来新一轮变革。"

    monkeypatch.setattr("idea_hub.services.llm.chat_text", fake_chat_text)

    resp = client.post("/api/v1/execute", auth=AUTH, json={"task_ids": [task_id]})
    assert resp.status_code == 200, resp.text
    exec_job = resp.json()["data"]["job_id"]
    assert _wait_job(config, exec_job) == "done"

    # 7) 产物读取（DB + 磁盘文件一致）
    out_path = tmp_path / "outputs" / "tasks" / str(task_id) / "output.md"
    assert out_path.is_file(), "output.md should be written to base_path"
    resp = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH)
    assert resp.status_code == 200, resp.text
    output = resp.json()["data"]
    assert output["filename"] == "output.md"
    assert "量子计算" in output["content"]

    # 8) 搜索命中（outputs_fts trigram 全文检索）
    resp = client.get("/api/v1/search", auth=AUTH, params={"q": "量子计算"})
    assert resp.status_code == 200
    hits = resp.json()["data"]["items"]
    assert any(
        it["entity_type"] == "output" and it["entity_id"] == task_id for it in hits
    ), f"output not found in search hits: {hits}"

    # 9) 统计正确
    resp = client.get("/api/v1/stats", auth=AUTH)
    assert resp.status_code == 200
    stats = resp.json()["data"]
    assert stats["queue"]["done"] == 1, stats["queue"]
    assert stats["hotspots"]["admit"] == 1, stats["hotspots"]
    assert stats["hotspots"]["discard"] == 1, stats["hotspots"]
    assert stats["today_produced"] == 1, stats
    assert stats["tokens"]["generation_total"] > 0, stats["tokens"]

    # 10) 通知存在
    resp = client.get("/api/v1/notifications", auth=AUTH)
    assert resp.status_code == 200
    types = {n["type"] for n in resp.json()["data"]["items"]}
    assert "collect_done" in types, types
    assert "execute_done" in types, types


def test_static_serves_frontend_dist(tmp_path):
    """S8.1: production app serves the built frontend from web/dist.

    Mirrors the module-level entry point: create_app(load(), static_dir=...)
    which is what uvicorn idea_hub.main:app runs.
    """
    from pathlib import Path

    dist = Path(__file__).resolve().parent.parent / "web" / "dist"
    config = make_config(tmp_path)
    client = client_for(config, static_dir=str(dist))
    resp = client.get("/")
    assert resp.status_code == 200, resp.text
    assert "text/html" in resp.headers["content-type"]
    # API routes are still reachable alongside the static mount
    resp = client.get("/api/v1/health", auth=AUTH)
    assert resp.status_code == 200
