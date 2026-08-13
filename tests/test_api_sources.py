"""Tests for the Idea Hub sources API (S2.4).

Covers: GET/POST /api/v1/sources, PATCH/DELETE /api/v1/sources/{id},
POST /api/v1/sources/{id}/toggle, POST /api/v1/sources/{id}/test.
Contract: unified {data, error} responses; type validated against the
collector registry (400 UNKNOWN_SOURCE_TYPE); DELETE with linked hot_items
returns 409 SOURCE_HAS_ITEMS; the test endpoint never writes to hot_items.
"""
from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.collectors.base import CollectorError, RawItem
from idea_hub.collectors.rss import RssCollector
from idea_hub.config import Config
from idea_hub.main import create_app

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


def _add_hot_item(config, source_id, title="热点条目"):
    conn = db.connect(config.db_path)
    conn.execute(
        "INSERT INTO hot_items (source_id, title, url) VALUES (?, ?, ?)",
        (source_id, title, f"http://example.com/item/{source_id}"),
    )
    conn.commit()
    conn.close()


# ---- auth ----

def test_sources_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/sources").status_code == 401
    assert client.post("/api/v1/sources", json=VALID_SOURCE).status_code == 401


# ---- create ----

def test_create_source_unknown_type_400(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post(
        "/api/v1/sources", auth=AUTH,
        json={"type": "not-a-real-type", "name": "x", "url": "http://x"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "UNKNOWN_SOURCE_TYPE"


def test_create_source_success_with_defaults(tmp_path):
    client = client_for(make_config(tmp_path))
    data = _create_source(client)
    assert data["id"] > 0
    assert data["type"] == "rss"
    assert data["name"] == "测试源"
    assert data["enabled"] is True
    assert data["ttl_hours"] == 24
    assert data["channel_config"] == {}


def test_create_source_with_all_fields(tmp_path):
    client = client_for(make_config(tmp_path))
    data = _create_source(
        client,
        type="hotlist",
        enabled=False,
        items_path="data.list",
        title_field="title",
        keywords="AI, 科技",
        ttl_hours=12,
        channel_config={"limit": 10},
    )
    assert data["enabled"] is False
    assert data["items_path"] == "data.list"
    assert data["keywords"] == "AI, 科技"
    assert data["ttl_hours"] == 12
    assert data["channel_config"] == {"limit": 10}


# ---- list ----

def test_list_sources_returns_created(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/sources", auth=AUTH).json()["data"] == []
    _create_source(client, name="A")
    _create_source(client, name="B", type="hotlist")
    body = client.get("/api/v1/sources", auth=AUTH).json()["data"]
    assert [s["name"] for s in body] == ["A", "B"]
    assert all("channel_config" in s for s in body)


# ---- patch ----

def test_patch_source_updates_fields(tmp_path):
    client = client_for(make_config(tmp_path))
    source = _create_source(client)
    resp = client.patch(
        f"/api/v1/sources/{source['id']}", auth=AUTH,
        json={"name": "新名字", "url": "http://new/feed.xml",
              "keywords": "AI", "ttl_hours": 48},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["name"] == "新名字"
    assert data["url"] == "http://new/feed.xml"
    assert data["keywords"] == "AI"
    assert data["ttl_hours"] == 48
    listed = client.get("/api/v1/sources", auth=AUTH).json()["data"]
    assert listed[0]["name"] == "新名字"


def test_patch_source_partial_keeps_other_fields(tmp_path):
    client = client_for(make_config(tmp_path))
    source = _create_source(client, url="http://old/feed.xml")
    resp = client.patch(
        f"/api/v1/sources/{source['id']}", auth=AUTH, json={"keywords": "只改关键词"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["keywords"] == "只改关键词"
    assert data["url"] == "http://old/feed.xml"
    assert data["name"] == "测试源"


def test_patch_source_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.patch("/api/v1/sources/999", auth=AUTH, json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


# ---- toggle ----

def test_toggle_source_flips_enabled(tmp_path):
    client = client_for(make_config(tmp_path))
    source = _create_source(client)
    assert source["enabled"] is True
    resp = client.post(f"/api/v1/sources/{source['id']}/toggle", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"]["enabled"] is False
    assert client.post(f"/api/v1/sources/{source['id']}/toggle", auth=AUTH).json()["data"]["enabled"] is True


def test_toggle_source_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post("/api/v1/sources/999/toggle", auth=AUTH)
    assert resp.status_code == 404


# ---- delete ----

def test_delete_source_without_items_success(tmp_path):
    client = client_for(make_config(tmp_path))
    source = _create_source(client)
    resp = client.delete(f"/api/v1/sources/{source['id']}", auth=AUTH)
    assert resp.status_code == 200
    assert client.get("/api/v1/sources", auth=AUTH).json()["data"] == []


def test_delete_source_with_hot_items_409(tmp_path):
    client = client_for(make_config(tmp_path))
    source = _create_source(client)
    _add_hot_item(make_config(tmp_path), source["id"])
    resp = client.delete(f"/api/v1/sources/{source['id']}", auth=AUTH)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SOURCE_HAS_ITEMS"
    # 来源仍存在
    listed = client.get("/api/v1/sources", auth=AUTH).json()["data"]
    assert [s["id"] for s in listed] == [source["id"]]


def test_delete_source_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.delete("/api/v1/sources/999", auth=AUTH)
    assert resp.status_code == 404


# ---- test endpoint ----

def _sample_items():
    return [
        RawItem(title=f"条目{i}", url=f"http://example.com/{i}",
                content_snapshot=f"快照{i}", source_id=0)
        for i in range(1, 4)
    ]


def test_test_endpoint_success_no_db_write(tmp_path, monkeypatch):
    client = client_for(make_config(tmp_path))
    source = _create_source(client)
    monkeypatch.setattr(RssCollector, "fetch", lambda self: _sample_items())
    resp = client.post(f"/api/v1/sources/{source['id']}/test", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True
    assert data["item_count"] == 3
    assert [it["title"] for it in data["sample_items"]] == ["条目1", "条目2", "条目3"]
    assert "error" not in data
    # 不落库
    conn = db.connect(make_config(tmp_path).db_path)
    count = conn.execute("SELECT COUNT(*) FROM hot_items").fetchone()[0]
    conn.close()
    assert count == 0


def test_test_endpoint_failure(tmp_path, monkeypatch):
    client = client_for(make_config(tmp_path))
    source = _create_source(client)

    def boom(self):
        raise CollectorError("network down")

    monkeypatch.setattr(RssCollector, "fetch", boom)
    resp = client.post(f"/api/v1/sources/{source['id']}/test", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert data["item_count"] == 0
    assert data["sample_items"] == []
    assert "network down" in data["error"]
    conn = db.connect(make_config(tmp_path).db_path)
    count = conn.execute("SELECT COUNT(*) FROM hot_items").fetchone()[0]
    conn.close()
    assert count == 0


def test_test_endpoint_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post("/api/v1/sources/999/test", auth=AUTH)
    assert resp.status_code == 404


def test_test_endpoint_unknown_type_ok_false(tmp_path):
    client = client_for(make_config(tmp_path))
    # 绕过 API 的 type 校验，直接造一条未知类型的来源行（如 v1 遗留数据）
    conn = db.connect(make_config(tmp_path).db_path)
    cur = conn.execute(
        "INSERT INTO sources (type, name, url) VALUES ('ghost-type', '遗留', 'http://x')"
    )
    conn.commit()
    source_id = cur.lastrowid
    conn.close()
    resp = client.post(f"/api/v1/sources/{source_id}/test", auth=AUTH)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is False
    assert "ghost-type" in data["error"]
