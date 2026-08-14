"""Task S7.1: notifications API tests.

Covers: GET /api/v1/notifications (list + pagination + level/type/unread_only
filters, created_at DESC), GET /api/v1/notifications/unread-count,
POST /api/v1/notifications/{id}/read (idempotent mark-read, 404 for missing),
POST /api/v1/notifications/read-all, auth 401. Contract: unified {data, error}
responses; list returns {items, total, page, size}.
"""
from fastapi.testclient import TestClient

from idea_hub import db
from idea_hub.config import Config
from idea_hub.main import create_app
from idea_hub.services.notify import emit

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


def _emit(config, *, type="collect_done", title="通知", body="内容",
          level="info", entity_type=None, entity_id=None):
    conn = db.connect(config.db_path)
    try:
        nid = emit(conn, type=type, title=title, body=body, level=level,
                   entity_type=entity_type, entity_id=entity_id)
    finally:
        conn.close()
    return nid


# ---- auth ----

def test_notifications_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/notifications").status_code == 401
    assert client.get("/api/v1/notifications/unread-count").status_code == 401
    assert client.post("/api/v1/notifications/1/read").status_code == 401
    assert client.post("/api/v1/notifications/read-all").status_code == 401


# ---- list ----

def test_list_empty(tmp_path):
    client = client_for(make_config(tmp_path))
    body = client.get("/api/v1/notifications", auth=AUTH).json()["data"]
    assert body == {"items": [], "total": 0, "page": 1, "size": 20}


def test_list_fields(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _emit(config, type="job_failed", title="任务失败", body="执行出错",
          level="error", entity_type="task", entity_id=7)
    item = client.get("/api/v1/notifications", auth=AUTH).json()["data"]["items"][0]
    assert item["id"] > 0
    assert item["type"] == "job_failed"
    assert item["title"] == "任务失败"
    assert item["body"] == "执行出错"
    assert item["level"] == "error"
    assert item["entity_type"] == "task"
    assert item["entity_id"] == 7
    assert item["is_read"] == 0
    assert item["created_at"]


def test_list_desc_order_and_pagination(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    ids = [_emit(config, title=f"通知{i}") for i in range(5)]

    body = client.get("/api/v1/notifications?page=1&size=2", auth=AUTH).json()["data"]
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["size"] == 2
    # newest first (id DESC tie-break on created_at)
    assert [it["id"] for it in body["items"]] == [ids[4], ids[3]]

    body = client.get("/api/v1/notifications?page=3&size=2", auth=AUTH).json()["data"]
    assert [it["id"] for it in body["items"]] == [ids[0]]

    body = client.get("/api/v1/notifications?size=100", auth=AUTH).json()["data"]
    assert body["total"] == 5


def test_list_level_filter(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _emit(config, title="info1", level="info")
    _emit(config, title="error1", level="error")
    _emit(config, title="error2", level="error")
    body = client.get("/api/v1/notifications?level=error", auth=AUTH).json()["data"]
    assert body["total"] == 2
    assert all(it["level"] == "error" for it in body["items"])


def test_list_type_filter(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _emit(config, type="collect_done", title="收集完成")
    _emit(config, type="job_failed", title="失败1")
    _emit(config, type="job_failed", title="失败2")
    body = client.get("/api/v1/notifications?type=job_failed", auth=AUTH).json()["data"]
    assert body["total"] == 2
    assert all(it["type"] == "job_failed" for it in body["items"])


def test_list_invalid_level_400(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/notifications?level=bogus", auth=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_list_unread_only(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    nid = _emit(config, title="未读")
    _emit(config, title="未读2")
    client.post(f"/api/v1/notifications/{nid}/read", auth=AUTH)
    body = client.get("/api/v1/notifications?unread_only=true", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["title"] == "未读2"


# ---- unread count ----

def test_unread_count(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    nid = _emit(config)
    _emit(config)
    body = client.get("/api/v1/notifications/unread-count", auth=AUTH).json()["data"]
    assert body == {"count": 2}
    client.post(f"/api/v1/notifications/{nid}/read", auth=AUTH)
    body = client.get("/api/v1/notifications/unread-count", auth=AUTH).json()["data"]
    assert body == {"count": 1}


# ---- mark read / read all ----

def test_mark_read_idempotent(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    nid = _emit(config)

    resp = client.post(f"/api/v1/notifications/{nid}/read", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"id": nid, "is_read": True}

    # second call is idempotent: still 200, still read
    resp = client.post(f"/api/v1/notifications/{nid}/read", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"id": nid, "is_read": True}

    conn = db.connect(config.db_path)
    try:
        is_read = conn.execute(
            "SELECT is_read FROM notifications WHERE id = ?", (nid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert is_read == 1


def test_mark_read_missing_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post("/api/v1/notifications/999/read", auth=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_read_all(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    _emit(config)
    _emit(config)
    _emit(config)
    resp = client.post("/api/v1/notifications/read-all", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"updated": 3}
    body = client.get("/api/v1/notifications/unread-count", auth=AUTH).json()["data"]
    assert body == {"count": 0}
