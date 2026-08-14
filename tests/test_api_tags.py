"""Tests for the Idea Hub tags API (S4.4).

Covers: GET /api/v1/tags (list with id/name/color/task_count aggregation),
auth 401, and empty-table behavior. PUT /api/v1/tasks/{id}/tags is deferred
to S5.1 (tasks CRUD). Contract: unified {data, error} responses; list
returns tags ordered by id, each with task_count = number of linked tasks.
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


def _add_tag(config, name, color="#3b82f6"):
    conn = db.connect(config.db_path)
    cur = conn.execute(
        "INSERT INTO tags (name, color) VALUES (?, ?)", (name, color)
    )
    conn.commit()
    conn.close()
    return cur.lastrowid


def _add_task(config, title="任务"):
    conn = db.connect(config.db_path)
    cur = conn.execute("INSERT INTO tasks (title) VALUES (?)", (title,))
    conn.commit()
    conn.close()
    return cur.lastrowid


def _link_task_tag(config, task_id, tag_id):
    conn = db.connect(config.db_path)
    conn.execute(
        "INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)",
        (task_id, tag_id),
    )
    conn.commit()
    conn.close()


# ---- auth ----

def test_tags_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/tags").status_code == 401


# ---- list ----

def test_list_empty(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/tags", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_list_fields_and_task_count(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    ai_id = _add_tag(config, "AI", color="#3b82f6")
    tech_id = _add_tag(config, "科技", color="#ef4444")
    _add_tag(config, "无任务标签", color="#22c55e")
    task1 = _add_task(config)
    task2 = _add_task(config)
    _link_task_tag(config, task1, ai_id)
    _link_task_tag(config, task2, ai_id)
    _link_task_tag(config, task1, tech_id)

    resp = client.get("/api/v1/tags", auth=AUTH)
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert [t["name"] for t in body] == ["AI", "科技", "无任务标签"]

    by_name = {t["name"]: t for t in body}
    assert by_name["AI"] == {
        "id": ai_id,
        "name": "AI",
        "color": "#3b82f6",
        "task_count": 2,
    }
    assert by_name["科技"]["task_count"] == 1
    assert by_name["无任务标签"]["task_count"] == 0
