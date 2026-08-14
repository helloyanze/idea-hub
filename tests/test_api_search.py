"""Task S7.3: unified search API tests.

Covers: GET /api/v1/search?q=&page=&size= across three FTS tables
(hot_items_fts / tasks_fts / outputs_fts, trigram): per-table top
page*size fetch, merge, group by entity_type (hot_item/task/output order,
bm25 rank within group), unified pagination; per-item shape
{entity_type, entity_id, title, snippet, score}; output entity_id = task_id;
snippet context window around match; auth 401. Contract: unified {data,
error} responses; list returns {items, total, page, size}.
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


# ---- auth ----

def test_search_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/search?q=人工智能").status_code == 401


# ---- basic matching ----

def test_search_short_query_empty(tmp_path):
    # trigram tokenizer: 1-2 char queries never match
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    _exec(config, "INSERT INTO hot_items (source_id, title) VALUES (1, '人工智能大模型')")
    body = client.get("/api/v1/search?q=智能", auth=AUTH).json()["data"]
    assert body == {"items": [], "total": 0, "page": 1, "size": 20}


def test_search_no_match_empty(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    _exec(config, "INSERT INTO hot_items (source_id, title) VALUES (1, '人工智能大模型')")
    body = client.get("/api/v1/search?q=量子计算", auth=AUTH).json()["data"]
    assert body == {"items": [], "total": 0, "page": 1, "size": 20}


def test_search_hits_all_three_types(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    # hot_item hit (title)
    _exec(config, "INSERT INTO hot_items (source_id, title, content_snapshot) "
                  "VALUES (1, '人工智能大模型迎来新突破', '多家公司发布最新进展')")
    # task hit (title)
    _exec(config, "INSERT INTO tasks (title, ai_summary) "
                  "VALUES ('人工智能内容创作任务', '关于人工智能的总结')")
    # output hit (content) for a different task
    _exec(config, "INSERT INTO tasks (title) VALUES ('产物任务')")
    _exec(config, "INSERT INTO outputs (task_id, version, content) "
                  "VALUES (2, 1, '这是一份人工智能相关的产物内容')")

    body = client.get("/api/v1/search?q=人工智能", auth=AUTH).json()["data"]
    assert body["total"] == 3
    items = body["items"]
    assert [it["entity_type"] for it in items] == ["hot_item", "task", "output"]
    for it in items:
        assert set(it.keys()) == {"entity_type", "entity_id", "title", "snippet", "score"}
        assert isinstance(it["score"], (int, float))
        assert it["title"]
        assert it["snippet"]
    # group ordering: hot_item group first, then task, then output
    hot = items[0]
    assert hot["entity_id"] == 1
    assert hot["title"] == "人工智能大模型迎来新突破"
    task = items[1]
    assert task["entity_id"] == 1
    assert task["title"] == "人工智能内容创作任务"
    out = items[2]
    assert out["title"] == "产物任务"


def test_search_output_entity_id_is_task_id(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    _exec(config, "INSERT INTO tasks (title) VALUES ('产物任务')")
    _exec(config, "INSERT INTO outputs (task_id, version, content) "
                  "VALUES (1, 1, '产物正文包含人工智能关键词')")
    body = client.get("/api/v1/search?q=人工智能", auth=AUTH).json()["data"]
    assert body["total"] == 1
    item = body["items"][0]
    assert item["entity_type"] == "output"
    assert item["entity_id"] == 1  # task_id, not outputs.id


def test_search_hot_item_snippet_context(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    content = "开头" + "填充" * 60 + "人工智能" + "填充" * 60 + "结尾"
    _exec(config, "INSERT INTO hot_items (source_id, title, content_snapshot) "
                  "VALUES (1, '热点标题', ?)", (content,))
    item = client.get("/api/v1/search?q=人工智能", auth=AUTH).json()["data"]["items"][0]
    assert "人工智能" in item["snippet"]
    # context window ~50 chars each side, so snippet is truncated vs full content
    assert len(item["snippet"]) < len(content)
    assert len(item["snippet"]) <= 150


def test_search_snippet_from_title_when_content_empty(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    _exec(config, "INSERT INTO hot_items (source_id, title) VALUES (1, '人工智能大模型')")
    item = client.get("/api/v1/search?q=人工智能", auth=AUTH).json()["data"]["items"][0]
    assert "人工智能" in item["snippet"]


# ---- pagination and grouping ----

def test_search_pagination_no_data_loss(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    # 1 hot_item + 3 tasks, all matching
    _exec(config, "INSERT INTO hot_items (source_id, title) VALUES (1, '人工智能热点A')")
    for i in range(1, 4):
        _exec(config, "INSERT INTO tasks (title) VALUES (?)", (f"人工智能任务{i}",))

    body = client.get("/api/v1/search?q=人工智能&page=1&size=2", auth=AUTH).json()["data"]
    assert body["total"] == 4
    assert body["page"] == 1
    assert body["size"] == 2
    page1_types = [it["entity_type"] for it in body["items"]]
    assert page1_types == ["hot_item", "task"]  # group order preserved
    page1_task_titles = {it["title"] for it in body["items"] if it["entity_type"] == "task"}

    body = client.get("/api/v1/search?q=人工智能&page=2&size=2", auth=AUTH).json()["data"]
    assert body["total"] == 4
    page2_titles = {it["title"] for it in body["items"]}
    # hot_item already consumed on page 1; remaining tasks must not be lost
    assert len(page2_titles) == 2
    all_titles = page1_task_titles | page2_titles
    assert all_titles == {"人工智能任务1", "人工智能任务2", "人工智能任务3"}


def test_search_bm25_rank_within_group(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _seed_source(config)
    # two tasks matching the keyword; order within group must follow bm25 rank
    _exec(config, "INSERT INTO tasks (title, ai_summary) "
                  "VALUES ('人工智能任务', '人工智能人工智能人工智能人工智能人工智能人工智能')")
    _exec(config, "INSERT INTO tasks (title, ai_summary) "
                  "VALUES ('人工智能任务二', '')")
    body = client.get("/api/v1/search?q=人工智能", auth=AUTH).json()["data"]
    tasks = [it for it in body["items"] if it["entity_type"] == "task"]
    assert len(tasks) == 2
    # bm25: lower score = better rank; dense keyword repetition ranks first
    assert tasks[0]["score"] <= tasks[1]["score"]
    assert tasks[0]["title"] == "人工智能任务"
