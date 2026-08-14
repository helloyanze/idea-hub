"""Tests for the Idea Hub tasks API (S5.1).

Covers: GET /api/v1/tasks (list + status/tag filters + q= FTS search via
tasks_fts trigram + pagination), GET /api/v1/tasks/{id} (detail with tags,
linked hotspot summaries, output summary {has_output, latest_version,
version_count, ai_summary}), POST /api/v1/tasks (manual create with defaults),
PATCH /api/v1/tasks/{id} (whitelisted fields), DELETE /api/v1/tasks/{id}
(cascade task_links/task_tags/outputs rows + filesystem dir, in_progress 409,
notifications/jobs kept), POST /tasks/{id}/move (full spec 5.2 migration
matrix minus done->in_progress, rowcount=0 -> 409), POST /tasks/{id}/redo
(done or fail_count>0 only), POST /tasks/{id}/reset-failures (fail_count>0
only), PUT /tasks/{id}/tags (replacement semantics, upsert by name).
Contract: unified {data, error} responses; list returns
{items, total, page, size} sorted by updated_at DESC, id DESC.
"""
from fastapi.testclient import TestClient
from pathlib import Path

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


def _create_task(client, **overrides):
    body = {"title": "测试任务"}
    body.update(overrides)
    resp = client.post("/api/v1/tasks", auth=AUTH, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _set_state(config, task_id, *, status=None, fail_count=None):
    """Bypass the API to set raw task state for transition tests."""
    conn = db.connect(config.db_path)
    sets, params = [], []
    if status is not None:
        sets.append("status = ?")
        params.append(status)
    if fail_count is not None:
        sets.append("fail_count = ?")
        params.append(fail_count)
    if sets:
        conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?",
            (*params, task_id),
        )
        conn.commit()
    conn.close()


def _insert_output(config, task_id, version, content="内容"):
    conn = db.connect(config.db_path)
    conn.execute(
        "INSERT INTO outputs (task_id, version, filename, content) "
        "VALUES (?, ?, 'output.md', ?)",
        (task_id, version, content),
    )
    conn.commit()
    conn.close()


# ---- auth ----

def test_tasks_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/tasks").status_code == 401
    assert client.get("/api/v1/tasks/1").status_code == 401
    assert client.post("/api/v1/tasks", json={"title": "x"}).status_code == 401
    assert client.patch("/api/v1/tasks/1", json={"title": "x"}).status_code == 401
    assert client.delete("/api/v1/tasks/1").status_code == 401
    assert client.post("/api/v1/tasks/1/move", json={"to_status": "done"}).status_code == 401
    assert client.post("/api/v1/tasks/1/redo", json={"note": "x"}).status_code == 401
    assert client.post("/api/v1/tasks/1/reset-failures").status_code == 401
    assert client.put("/api/v1/tasks/1/tags", json={"names": ["x"]}).status_code == 401


# ---- create ----

def test_create_defaults(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client, title="默认任务")
    assert task["status"] == "todo"
    assert task["content_type"] == "article"
    assert task["feasibility_score"] == 0
    assert task["score_breakdown"] == {}
    assert task["fail_count"] == 0
    assert task["token_used"] == 0
    assert task["target_desc"] == ""
    assert task["expire_at"] is None
    assert task["tags"] == []
    assert task["hotspots"] == []
    assert task["output"] == {
        "has_output": False,
        "latest_version": None,
        "version_count": 0,
        "ai_summary": "",
    }


def test_create_full_fields(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(
        client,
        title="完整任务",
        content_type="video_script",
        idea_summary="一句话摘要",
        feasibility_score=9,
        score_breakdown={"facts": 4, "value": 5},
        target_desc="目标描述",
        notes="备注内容",
        expire_at="2026-09-01 12:00:00",
    )
    assert task["content_type"] == "video_script"
    assert task["idea_summary"] == "一句话摘要"
    assert task["feasibility_score"] == 9
    assert task["score_breakdown"] == {"facts": 4, "value": 5}
    assert task["target_desc"] == "目标描述"
    assert task["notes"] == "备注内容"
    assert task["expire_at"] == "2026-09-01 12:00:00"


def test_create_missing_title_422(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post("/api/v1/tasks", auth=AUTH, json={"content_type": "article"})
    assert resp.status_code == 422


def test_create_invalid_content_type_400(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post(
        "/api/v1/tasks", auth=AUTH, json={"title": "x", "content_type": "podcast"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_create_with_hotspot_link(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    resp = client.post(
        "/api/v1/sources",
        auth=AUTH,
        json={"type": "rss", "name": "源", "url": "http://example.com/feed.xml"},
    )
    source = resp.json()["data"]
    conn = db.connect(config.db_path)
    cur = conn.execute(
        "INSERT INTO hot_items (source_id, title, url, collected_date) "
        "VALUES (?, '热点', 'http://example.com/hot', '2026-08-01')",
        (source["id"],),
    )
    conn.commit()
    hot_id = cur.lastrowid
    conn.close()

    task = _create_task(client, title="关联任务", hotspot_id=hot_id)
    assert [h["id"] for h in task["hotspots"]] == [hot_id]
    assert task["hotspots"][0]["title"] == "热点"


def test_create_with_missing_hotspot_400(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post(
        "/api/v1/tasks", auth=AUTH, json={"title": "x", "hotspot_id": 999}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


# ---- list ----

def test_list_empty(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/tasks", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"items": [], "total": 0, "page": 1, "size": 20}


def test_list_pagination_and_order(tmp_path):
    client = client_for(make_config(tmp_path))
    for i in range(5):
        _create_task(client, title=f"任务{i + 1}")

    body = client.get("/api/v1/tasks?page=1&size=2", auth=AUTH).json()["data"]
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["size"] == 2
    assert [it["title"] for it in body["items"]] == ["任务5", "任务4"]

    body = client.get("/api/v1/tasks?page=3&size=2", auth=AUTH).json()["data"]
    assert [it["title"] for it in body["items"]] == ["任务1"]


def test_list_status_filter(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    t_todo = _create_task(client, title="待办")
    t_waiting = _create_task(client, title="等待")
    t_done = _create_task(client, title="完成")
    _set_state(config, t_waiting["id"], status="waiting")
    _set_state(config, t_done["id"], status="done")

    body = client.get("/api/v1/tasks?status=todo", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["id"] == t_todo["id"]

    body = client.get("/api/v1/tasks?status=waiting", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["id"] == t_waiting["id"]

    body = client.get("/api/v1/tasks?status=done", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["id"] == t_done["id"]


def test_list_status_invalid_400(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/tasks?status=bogus", auth=AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_list_q_search_via_tasks_fts(tmp_path):
    client = client_for(make_config(tmp_path))
    _create_task(client, title="人工智能大模型研究")
    _create_task(client, title="无关标题", idea_summary="量子计算前沿进展")
    _create_task(client, title="完全无关")

    # 4-char keyword matches title AND idea_summary (trigram substring)
    body = client.get("/api/v1/tasks?q=人工智能", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["title"] == "人工智能大模型研究"

    body = client.get("/api/v1/tasks?q=量子计算", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["title"] == "无关标题"


def test_list_q_short_keyword_empty(tmp_path):
    client = client_for(make_config(tmp_path))
    _create_task(client, title="人工智能大模型")
    body = client.get("/api/v1/tasks?q=智能", auth=AUTH).json()["data"]
    assert body == {"items": [], "total": 0, "page": 1, "size": 20}


def test_list_q_combined_with_status(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    a = _create_task(client, title="人工智能待办")
    b = _create_task(client, title="人工智能完成")
    _set_state(config, b["id"], status="done")

    body = client.get("/api/v1/tasks?q=人工智能&status=done", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["id"] == b["id"]
    _ = a


def test_list_tag_filter(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    t1 = _create_task(client, title="带标签任务")
    t2 = _create_task(client, title="无标签任务")
    resp = client.put(
        f"/api/v1/tasks/{t1['id']}/tags", auth=AUTH, json={"names": ["深度"]}
    )
    assert resp.status_code == 200, resp.text
    _ = t2

    body = client.get("/api/v1/tasks?tag=深度", auth=AUTH).json()["data"]
    assert body["total"] == 1
    assert body["items"][0]["id"] == t1["id"]

    body = client.get("/api/v1/tasks?tag=不存在", auth=AUTH).json()["data"]
    assert body["total"] == 0


def test_list_items_include_tags(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client, title="带标签任务")
    client.put(f"/api/v1/tasks/{task['id']}/tags", auth=AUTH, json={"names": ["深度"]})
    item = client.get("/api/v1/tasks", auth=AUTH).json()["data"]["items"][0]
    assert [t["name"] for t in item["tags"]] == ["深度"]
    assert "color" in item["tags"][0]


# ---- detail ----

def test_detail_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.get("/api/v1/tasks/999", auth=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_detail_with_tags_hotspots_and_output_summary(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task = _create_task(client, title="详情任务")

    # attach tags
    client.put(f"/api/v1/tasks/{task['id']}/tags", auth=AUTH, json={"names": ["深", "AI"]})

    # link a hotspot
    resp = client.post(
        "/api/v1/sources",
        auth=AUTH,
        json={"type": "rss", "name": "源", "url": "http://example.com/f.xml"},
    )
    conn = db.connect(config.db_path)
    cur = conn.execute(
        "INSERT INTO hot_items (source_id, title, url, collected_date) "
        "VALUES (?, '热点标题', 'http://example.com/hot', '2026-08-02')",
        (resp.json()["data"]["id"],),
    )
    conn.commit()
    hot_id = cur.lastrowid
    conn.execute("INSERT INTO task_links (task_id, hot_item_id) VALUES (?, ?)",
                 (task["id"], hot_id))
    conn.commit()

    # two output versions + ai_summary
    _insert_output(config, task["id"], 1, "初版")
    _insert_output(config, task["id"], 2, "修订版")
    conn.execute("UPDATE tasks SET ai_summary = '文章要点' WHERE id = ?", (task["id"],))
    conn.commit()
    conn.close()

    detail = client.get(f"/api/v1/tasks/{task['id']}", auth=AUTH).json()["data"]
    assert [t["name"] for t in detail["tags"]] == ["深", "AI"]
    assert all("color" in t for t in detail["tags"])
    assert [h["id"] for h in detail["hotspots"]] == [hot_id]
    assert detail["hotspots"][0]["title"] == "热点标题"
    assert detail["output"] == {
        "has_output": True,
        "latest_version": 2,
        "version_count": 2,
        "ai_summary": "文章要点",
    }


# ---- patch ----

def test_patch_updates_fields(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client, title="原标题")
    resp = client.patch(
        f"/api/v1/tasks/{task['id']}",
        auth=AUTH,
        json={
            "title": "新标题",
            "idea_summary": "新摘要",
            "feasibility_score": 7,
            "notes": "新备注",
            "content_type": "tweet",
        },
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()["data"]
    assert updated["title"] == "新标题"
    assert updated["idea_summary"] == "新摘要"
    assert updated["feasibility_score"] == 7
    assert updated["notes"] == "新备注"
    assert updated["content_type"] == "tweet"
    assert updated["status"] == "todo"


def test_patch_invalid_content_type_400(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client)
    resp = client.patch(
        f"/api/v1/tasks/{task['id']}", auth=AUTH, json={"content_type": "podcast"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_patch_empty_body_ok(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client, title="原标题")
    resp = client.patch(f"/api/v1/tasks/{task['id']}", auth=AUTH, json={})
    assert resp.status_code == 200
    assert resp.json()["data"]["title"] == "原标题"


def test_patch_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.patch("/api/v1/tasks/999", auth=AUTH, json={"title": "x"})
    assert resp.status_code == 404


# ---- delete ----

def test_delete_cascade(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task = _create_task(client, title="待删任务")
    client.put(f"/api/v1/tasks/{task['id']}/tags", auth=AUTH, json={"names": ["删"]})

    # link hotspot + notification + output rows + filesystem dir
    resp = client.post(
        "/api/v1/sources",
        auth=AUTH,
        json={"type": "rss", "name": "源", "url": "http://example.com/f.xml"},
    )
    conn = db.connect(config.db_path)
    cur = conn.execute(
        "INSERT INTO hot_items (source_id, title, url, collected_date) "
        "VALUES (?, '热点', 'http://example.com/hot', '2026-08-01')",
        (resp.json()["data"]["id"],),
    )
    conn.commit()
    hot_id = cur.lastrowid
    conn.execute("INSERT INTO task_links (task_id, hot_item_id) VALUES (?, ?)",
                 (task["id"], hot_id))
    conn.execute(
        "INSERT INTO notifications (type, title, entity_type, entity_id) "
        "VALUES ('execute_done', '通知', 'task', ?)",
        (task["id"],),
    )
    conn.commit()
    conn.close()
    _insert_output(config, task["id"], 1)
    task_dir = Path(config.base_path) / "outputs" / "tasks" / str(task["id"])
    task_dir.mkdir(parents=True)
    (task_dir / "output.md").write_text("产物", encoding="utf-8")

    resp = client.delete(f"/api/v1/tasks/{task['id']}", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"] == {"deleted": True}

    assert client.get(f"/api/v1/tasks/{task['id']}", auth=AUTH).status_code == 404
    conn = db.connect(config.db_path)
    assert conn.execute(
        "SELECT COUNT(*) FROM task_links WHERE task_id = ?", (task["id"],)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM task_tags WHERE task_id = ?", (task["id"],)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM outputs WHERE task_id = ?", (task["id"],)
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE entity_id = ?", (task["id"],)
    ).fetchone()[0] == 1  # notifications kept
    conn.close()
    assert not task_dir.exists()


def test_delete_in_progress_409(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task = _create_task(client)
    _set_state(config, task["id"], status="in_progress")
    resp = client.delete(f"/api/v1/tasks/{task['id']}", auth=AUTH)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "TASK_IN_PROGRESS"
    # still exists
    assert client.get(f"/api/v1/tasks/{task['id']}", auth=AUTH).status_code == 200


def test_delete_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.delete("/api/v1/tasks/999", auth=AUTH)
    assert resp.status_code == 404


# ---- move (spec 5.2 matrix, minus done->in_progress) ----

def _move(client, task_id, to_status):
    return client.post(f"/api/v1/tasks/{task_id}/move", auth=AUTH, json={"to_status": to_status})


def _move_ok(client, task_id, to_status):
    resp = _move(client, task_id, to_status)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _move_conflict(client, task_id, to_status):
    resp = _move(client, task_id, to_status)
    assert resp.status_code == 409, resp.text
    return resp.json()["error"]


def test_move_matrix_all_combinations(tmp_path):
    """Full spec 5.2 matrix: move allows every cell except done->in_progress."""
    config = make_config(tmp_path)
    client = client_for(config)

    def fresh(status):
        t = _create_task(client, title=f"任务-{status}")
        if status != "todo":
            _set_state(config, t["id"], status=status)
        return t

    # allowed transitions
    t = fresh("todo")
    _move_ok(client, t["id"], "waiting")
    t = fresh("todo")
    _move_ok(client, t["id"], "in_progress")
    t = fresh("todo")
    data = _move_ok(client, t["id"], "done")
    assert data["completed_at"] is not None  # entering done records completed_at

    t = fresh("waiting")
    _move_ok(client, t["id"], "todo")
    t = fresh("waiting")
    _move_ok(client, t["id"], "in_progress")
    t = fresh("waiting")
    _move_ok(client, t["id"], "done")

    t = fresh("in_progress")
    _move_ok(client, t["id"], "waiting")
    t = fresh("in_progress")
    _move_ok(client, t["id"], "done")

    t = fresh("done")
    data = _move_ok(client, t["id"], "todo")
    assert data["completed_at"] is None  # leaving done clears completed_at

    # forbidden transitions -> 409, state unchanged
    t = fresh("in_progress")
    err = _move_conflict(client, t["id"], "todo")
    assert err["code"] == "INVALID_STATUS_TRANSITION"

    t = fresh("done")
    err = _move_conflict(client, t["id"], "waiting")
    assert err["code"] == "INVALID_STATUS_TRANSITION"

    t = fresh("done")
    err = _move_conflict(client, t["id"], "in_progress")
    assert err["code"] == "INVALID_STATUS_TRANSITION"

    # self-transition -> 409 (rowcount 0)
    t = fresh("todo")
    err = _move_conflict(client, t["id"], "todo")
    assert err["code"] == "INVALID_STATUS_TRANSITION"

    # states preserved after forbidden attempts
    assert client.get(f"/api/v1/tasks/{t['id']}", auth=AUTH).json()["data"]["status"] == "todo"


def test_move_invalid_to_status_400(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client)
    resp = _move(client, task["id"], "archived")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"


def test_move_missing_to_status_422(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client)
    resp = client.post(f"/api/v1/tasks/{task['id']}/move", auth=AUTH, json={})
    assert resp.status_code == 422


def test_move_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = _move(client, 999, "done")
    assert resp.status_code == 404


# ---- redo ----

def test_redo_done_task(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task = _create_task(client)
    _set_state(config, task["id"], status="done", fail_count=1)
    resp = client.post(
        f"/api/v1/tasks/{task['id']}/redo", auth=AUTH, json={"note": "改个方向"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "waiting"
    assert data["fail_count"] == 0
    assert "改个方向" in data["redo_note"]
    assert data["redo_note"].startswith("20")  # timestamp prefix


def test_redo_failed_task(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task = _create_task(client)
    _set_state(config, task["id"], status="waiting", fail_count=3)
    resp = client.post(f"/api/v1/tasks/{task['id']}/redo", auth=AUTH, json={})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "waiting"
    assert data["fail_count"] == 0
    assert data["redo_note"]  # timestamp only


def test_redo_not_allowed_409(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task = _create_task(client)  # todo, fail_count=0
    resp = client.post(f"/api/v1/tasks/{task['id']}/redo", auth=AUTH, json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "REDO_NOT_ALLOWED"


def test_redo_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post("/api/v1/tasks/999/redo", auth=AUTH, json={})
    assert resp.status_code == 404


# ---- reset-failures ----

def test_reset_failures_clears_count(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task = _create_task(client)
    _set_state(config, task["id"], status="in_progress", fail_count=3)
    resp = client.post(f"/api/v1/tasks/{task['id']}/reset-failures", auth=AUTH)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["fail_count"] == 0
    assert data["status"] == "in_progress"  # state unchanged


def test_reset_failures_zero_409(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client)
    resp = client.post(f"/api/v1/tasks/{task['id']}/reset-failures", auth=AUTH)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "NO_FAILURES"


def test_reset_failures_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.post("/api/v1/tasks/999/reset-failures", auth=AUTH)
    assert resp.status_code == 404


# ---- tags PUT (replacement semantics) ----

def test_tags_put_replace_semantics(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client, title="标签任务")

    resp = client.put(
        f"/api/v1/tasks/{task['id']}/tags", auth=AUTH, json={"names": ["深度", "AI"]}
    )
    assert resp.status_code == 200, resp.text
    tags = resp.json()["data"]["tags"]
    assert [t["name"] for t in tags] == ["深度", "AI"]
    first_ids = {t["name"]: t["id"] for t in tags}

    # replacement: keep "AI" (same id), drop "深度", add "新题"
    resp = client.put(
        f"/api/v1/tasks/{task['id']}/tags", auth=AUTH, json={"names": ["AI", "新题"]}
    )
    assert resp.status_code == 200
    tags = resp.json()["data"]["tags"]
    assert [t["name"] for t in tags] == ["AI", "新题"]
    by_name = {t["name"]: t["id"] for t in tags}
    assert by_name["AI"] == first_ids["AI"]  # reused, not recreated
    assert by_name["新题"] != first_ids["深度"]
    assert all("color" in t for t in tags)


def test_tags_put_empty_clears_all(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client)
    client.put(f"/api/v1/tasks/{task['id']}/tags", auth=AUTH, json={"names": ["深度"]})
    resp = client.put(f"/api/v1/tasks/{task['id']}/tags", auth=AUTH, json={"names": []})
    assert resp.status_code == 200
    assert resp.json()["data"]["tags"] == []


def test_tags_put_duplicate_names_deduped(tmp_path):
    client = client_for(make_config(tmp_path))
    task = _create_task(client)
    resp = client.put(
        f"/api/v1/tasks/{task['id']}/tags",
        auth=AUTH,
        json={"names": ["深度", "深度", " AI "]},
    )
    assert resp.status_code == 200
    tags = resp.json()["data"]["tags"]
    assert [t["name"] for t in tags] == ["深度", "AI"]
    # no orphan tag rows from dedup
    conn = db.connect(client.app.state.config.db_path)
    count = conn.execute("SELECT COUNT(*) FROM tags WHERE name = '深度'").fetchone()[0]
    conn.close()
    assert count == 1


def test_tags_put_not_found_404(tmp_path):
    client = client_for(make_config(tmp_path))
    resp = client.put("/api/v1/tasks/999/tags", auth=AUTH, json={"names": ["x"]})
    assert resp.status_code == 404
