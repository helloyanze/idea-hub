"""Tests for the Idea Hub hotspots API (S3.3).

Covers: GET /api/v1/hotspots (list + pagination + verdict/source_id filters
+ q= FTS search via hot_items_fts trigram), GET /api/v1/hotspots/{id}
(detail with parsed score_breakdown and linked_task_count), auth 401 and
404 handling. Contract: unified {data, error} responses; list returns
{items, total, page, size} sorted by collected_date DESC.
"""
from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.config import Config
from idea_hub.main import create_app

AUTH = ("admin", "secret")


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
    body = {"type": "rss", "name": "测试源", "url": "http://example.com/feed.xml"}
    body.update(overrides)
    resp = client.post("/api/v1/sources", auth=AUTH, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _add_hot_item(
    config,
    source_id,
    *,
    title="热点条目",
    url=None,
    content_snapshot="",
    final_score=0.0,
    score_breakdown="{}",
    verdict="admit",
    collected_date="2026-08-01",
):
    conn = db.connect(config.db_path)
    cur = conn.execute(
        "INSERT INTO hot_items (source_id, title, url, content_snapshot, final_score, "
        "score_breakdown, verdict, collected_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            source_id,
            title,
            url or f"http://example.com/item/{title}-{collected_date}",
            content_snapshot,
            final_score,
            score_breakdown,
            verdict,
            collected_date,
        ),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _add_task(config, hot_item_id):
    conn = db.connect(config.db_path)
    cur = conn.execute("INSERT INTO tasks (title) VALUES (?)", ("任务",))
    conn.execute(
        "INSERT INTO task_links (task_id, hot_item_id) VALUES (?, ?)",
        (cur.lastrowid, hot_item_id),
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


# ---- auth ----

def test_hotspots_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/hotspots").status_code == 401
    assert client.get("/api/v1/hotspots/1").status_code == 401


# ---- list ----

def test_list_empty(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/hotspots", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body == {"items": [], "total": 0, "page": 1, "size": 20}


def test_list_pagination_and_order(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client)
    for day in range(1, 6):
        _add_hot_item(config, source["id"], title=f"条目{day}", collected_date=f"2026-08-0{day}")

    body = client.get("/api/v1/hotspots?page=1&size=2", auth=AUTH).json()["data"]
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["size"] == 2
    assert [it["title"] for it in body["items"]] == ["条目5", "条目4"]

    body = client.get("/api/v1/hotspots?page=3&size=2", auth=AUTH).json()["data"]
    assert [it["title"] for it in body["items"]] == ["条目1"]

    body = client.get("/api/v1/hotspots?size=100", auth=AUTH).json()["data"]
    assert body["total"] == 5


def test_list_fields(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client, name="科技源")
    _add_hot_item(
        config,
        source["id"],
        title="人工智能大模型",
        content_snapshot="快照内容",
        final_score=8.5,
        score_breakdown='{"facts": 3, "value": 5, "total": 8.5}',
        verdict="admit",
    )
    item = client.get("/api/v1/hotspots", auth=AUTH).json()["data"]["items"][0]
    assert item["id"] > 0
    assert item["source_id"] == source["id"]
    assert item["source_name"] == "科技源"
    assert item["title"] == "人工智能大模型"
    assert item["url"].startswith("http://")
    assert item["content_snapshot"] == "快照内容"
    assert item["final_score"] == 8.5
    assert item["score_breakdown"] == {"facts": 3, "value": 5, "total": 8.5}
    assert item["verdict"] == "admit"
    assert item["collected_date"] == "2026-08-01"
    assert "linked_task_count" in item


# ---- filters ----

def test_list_verdict_filter(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client)
    for i in range(3):
        _add_hot_item(config, source["id"], title=f"admit{i}", verdict="admit",
                      collected_date=f"2026-08-0{i + 1}")
    for i in range(2):
        _add_hot_item(config, source["id"], title=f"discard{i}", verdict="discard",
                      collected_date=f"2026-08-1{i}")

    body = client.get("/api/v1/hotspots?verdict=discard", auth=AUTH).json()["data"]
    assert body["total"] == 2
    assert all(it["verdict"] == "discard" for it in body["items"])

    body = client.get("/api/v1/hotspots?verdict=admit", auth=AUTH).json()["data"]
    assert body["total"] == 3
    assert all(it["verdict"] == "admit" for it in body["items"])


def test_list_verdict_invalid_400(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client)
    _add_hot_item(config, source["id"])
    resp = client.get("/api/v1/hotspots?verdict=bogus", auth=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_list_source_id_filter(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source_a = _create_source(client, name="源A", url="http://a/feed.xml")
    source_b = _create_source(client, name="源B", url="http://b/feed.xml")
    _add_hot_item(config, source_a["id"], title="A1", collected_date="2026-08-01")
    _add_hot_item(config, source_a["id"], title="A2", collected_date="2026-08-02")
    _add_hot_item(config, source_b["id"], title="B1", collected_date="2026-08-03")

    body = client.get(f"/api/v1/hotspots?source_id={source_b['id']}", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert [it["title"] for it in body["items"]] == ["B1"]
    assert all(it["source_id"] == source_b["id"] for it in body["items"])


# ---- q= FTS search ----

def test_list_q_search_hits_title_and_content(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client)
    _add_hot_item(config, source["id"], title="人工智能大模型迎来新突破",
                  content_snapshot="多家公司发布最新进展", collected_date="2026-08-01")
    _add_hot_item(config, source["id"], title="量子计算研究获进展",
                  content_snapshot="科研团队宣布人工智能应用落地", collected_date="2026-08-02")
    _add_hot_item(config, source["id"], title="无关条目", collected_date="2026-08-03")

    # 4-char keyword matches title AND content_snapshot (trigram substring)
    body = client.get("/api/v1/hotspots?q=人工智能", auth=AUTH).json()["data"]
    assert body["total"] == 2
    assert {it["title"] for it in body["items"]} == {"人工智能大模型迎来新突破", "量子计算研究获进展"}

    body = client.get("/api/v1/hotspots?q=量子计算", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["title"] == "量子计算研究获进展"


def test_list_q_short_keyword_empty(tmp_path):
    # trigram tokenizer: 1-2 char queries never match (expected empty)
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client)
    _add_hot_item(config, source["id"], title="人工智能大模型", collected_date="2026-08-01")
    body = client.get("/api/v1/hotspots?q=智能", auth=AUTH).json()["data"]
    assert body == {"items": [], "total": 0, "page": 1, "size": 20}


def test_list_q_combined_with_verdict(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client)
    _add_hot_item(config, source["id"], title="人工智能大模型", verdict="admit",
                  collected_date="2026-08-01")
    _add_hot_item(config, source["id"], title="人工智能被丢弃", verdict="discard",
                  collected_date="2026-08-02")
    body = client.get("/api/v1/hotspots?q=人工智能&verdict=admit", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["title"] == "人工智能大模型"


# ---- detail ----

def test_detail_with_score_breakdown_and_linked_task(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client, name="科技源")
    item_id = _add_hot_item(
        config,
        source["id"],
        title="人工智能大模型",
        content_snapshot="快照",
        final_score=8.5,
        score_breakdown='{"facts": 3, "value": 5, "total": 8.5}',
        verdict="admit",
        collected_date="2026-08-01",
    )
    _add_task(config, item_id)

    resp = client.get(f"/api/v1/hotspots/{item_id}", auth=AUTH)
    assert resp.status_code == 200
    item = resp.json()["data"]
    assert item["id"] == item_id
    assert item["source_name"] == "科技源"
    assert item["score_breakdown"] == {"facts": 3, "value": 5, "total": 8.5}
    assert item["linked_task_count"] == 1
    assert item["final_score"] == 8.5
    assert item["verdict"] == "admit"


def test_detail_malformed_score_breakdown_null(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    source = _create_source(client)
    item_id = _add_hot_item(config, source["id"], score_breakdown="not-json")
    item = client.get(f"/api/v1/hotspots/{item_id}", auth=AUTH).json()["data"]
    assert item["score_breakdown"] is None


def test_detail_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/hotspots/999", auth=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
