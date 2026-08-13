# tests/test_source_edit.py
"""来源编辑：models.update_source + PATCH /api/sources/{id}。"""
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from idea_hub import db, models, server


def _conn(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    return conn


def _mk_source(tmp_path: Path) -> int:
    conn = _conn(tmp_path)
    sid = models.create_source(conn, type="rss", name="旧名", url="http://old",
                               keywords="")
    conn.close()
    return sid


def test_update_source_fields(tmp_path):
    sid = _mk_source(tmp_path)
    conn = _conn(tmp_path)
    models.update_source(conn, sid, name="新名", url="http://new", keywords="AI,模型",
                         ttl_hours=48)
    row = next(s for s in models.list_sources(conn) if s["id"] == sid)
    assert row["name"] == "新名"
    assert row["url"] == "http://new"
    assert row["keywords"] == "AI,模型"
    assert row["ttl_hours"] == 48
    assert row["enabled"] == 1  # 未更新字段保持不变


def test_update_source_partial_none_ignored(tmp_path):
    sid = _mk_source(tmp_path)
    conn = _conn(tmp_path)
    models.update_source(conn, sid, name="仅改名", url=None, keywords=None)
    row = next(s for s in models.list_sources(conn) if s["id"] == sid)
    assert row["name"] == "仅改名"
    assert row["url"] == "http://old"  # None 不覆盖


def test_update_source_not_found_raises(tmp_path):
    conn = _conn(tmp_path)
    try:
        models.update_source(conn, 999, name="x")
        assert False, "应抛异常"
    except ValueError:
        pass


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "t.db"))
    return TestClient(server.app)


def test_patch_source_endpoint(tmp_path, monkeypatch):
    sid = _mk_source(tmp_path)
    client = _client(tmp_path, monkeypatch)
    r = client.patch(f"/api/sources/{sid}",
                     json={"name": "百度热搜", "url": "http://new-url",
                           "keywords": "AI", "ttl_hours": 12})
    assert r.status_code == 200
    conn = _conn(tmp_path)
    row = next(s for s in models.list_sources(conn) if s["id"] == sid)
    assert row["name"] == "百度热搜"
    assert row["url"] == "http://new-url"
    assert row["keywords"] == "AI"
    assert row["ttl_hours"] == 12


def test_patch_source_partial(tmp_path, monkeypatch):
    sid = _mk_source(tmp_path)
    client = _client(tmp_path, monkeypatch)
    r = client.patch(f"/api/sources/{sid}", json={"keywords": "只改关键词"})
    assert r.status_code == 200
    conn = _conn(tmp_path)
    row = next(s for s in models.list_sources(conn) if s["id"] == sid)
    assert row["keywords"] == "只改关键词"
    assert row["url"] == "http://old"  # 未传字段不变


def test_patch_source_not_found(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.patch("/api/sources/999", json={"name": "x"})
    assert r.status_code == 404
