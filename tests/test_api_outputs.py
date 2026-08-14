"""Task S6.3: versioned outputs API — optimistic lock, read-time validation, file rebuild.

Covers: GET /api/v1/tasks/{id}/output (latest with read-time validation:
file changed externally -> DB backwritten without version bump; file missing ->
rebuilt from DB; no output -> 404), PUT /output (atomic version increment,
base_version optimistic lock -> 409 VERSION_CONFLICT, filename inherited,
ai_summary propagated), POST /output/upload (original filename stored,
version increment), GET /output/versions (metadata without content),
GET /output/versions/{version} (content, 404 for missing version), auth 401.
"""
from pathlib import Path

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


def _create_task(client):
    resp = client.post("/api/v1/tasks", auth=AUTH, json={"title": "产物任务"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _put_output(client, task_id, content, **extra):
    body = {"content": content}
    body.update(extra)
    return client.put(f"/api/v1/tasks/{task_id}/output", auth=AUTH, json=body)


def _file_path(config, task_id):
    return Path(config.base_path) / "outputs" / "tasks" / str(task_id) / "output.md"


def _db_content(config, task_id, version=None):
    conn = db.connect(config.db_path)
    try:
        if version is None:
            row = conn.execute(
                "SELECT content FROM outputs WHERE task_id = ? ORDER BY version DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT content FROM outputs WHERE task_id = ? AND version = ?",
                (task_id, version),
            ).fetchone()
        return row["content"] if row else None
    finally:
        conn.close()


def _version_count(config, task_id):
    conn = db.connect(config.db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM outputs WHERE task_id = ?", (task_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# ---- auth ----

def test_outputs_no_auth_401(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/tasks/1/output").status_code == 401
    assert client.put("/api/v1/tasks/1/output", json={"content": "x"}).status_code == 401
    assert client.post("/api/v1/tasks/1/output/upload", json={"filename": "a.md", "content": "x"}).status_code == 401
    assert client.get("/api/v1/tasks/1/output/versions").status_code == 401
    assert client.get("/api/v1/tasks/1/output/versions/1").status_code == 401


# ---- GET latest ----

def test_get_output_none_404(tmp_path):
    client = client_for(make_config(tmp_path))
    task_id = _create_task(client)
    resp = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_get_output_missing_task_404(tmp_path):
    client = client_for(make_config(tmp_path))
    assert client.get("/api/v1/tasks/999/output", auth=AUTH).status_code == 404


def test_get_output_returns_latest_metadata(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "第一版")
    _put_output(client, task_id, "第二版")

    body = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH).json()["data"]
    assert body["task_id"] == task_id
    assert body["version"] == 2
    assert body["content"] == "第二版"
    assert body["filename"] == "output.md"
    assert "id" in body
    assert "ai_summary" in body
    assert "created_at" in body


def test_get_output_ai_summary_from_task(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "正文", ai_summary="要点")
    body = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH).json()["data"]
    assert body["ai_summary"] == "要点"


# ---- PUT versioning + optimistic lock ----

def test_put_creates_first_version(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)

    resp = _put_output(client, task_id, "首版内容", base_version=0)
    assert resp.status_code == 200, resp.text
    meta = resp.json()["data"]
    assert meta["version"] == 1
    assert meta["filename"] == "output.md"

    # file written to disk
    assert _file_path(config, task_id).read_text(encoding="utf-8") == "首版内容"


def test_put_without_base_version_creates_v1(tmp_path):
    client = client_for(make_config(tmp_path))
    task_id = _create_task(client)
    resp = _put_output(client, task_id, "无锁首版")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["version"] == 1


def test_put_increments_version_atomically(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "v1")
    _put_output(client, task_id, "v2", base_version=1)
    _put_output(client, task_id, "v3", base_version=2)

    assert _version_count(config, task_id) == 3
    body = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH).json()["data"]
    assert body["version"] == 3
    assert body["content"] == "v3"
    assert _file_path(config, task_id).read_text(encoding="utf-8") == "v3"


def test_put_stale_base_version_409(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "v1")
    _put_output(client, task_id, "v2")

    resp = _put_output(client, task_id, "过期编辑", base_version=1)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VERSION_CONFLICT"
    # no new version created, latest untouched
    assert _version_count(config, task_id) == 2
    assert _db_content(config, task_id) == "v2"


def test_put_base_version_zero_with_existing_output_409(tmp_path):
    client = client_for(make_config(tmp_path))
    task_id = _create_task(client)
    _put_output(client, task_id, "v1")
    resp = _put_output(client, task_id, "重复首版", base_version=0)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "VERSION_CONFLICT"


def test_put_updates_ai_summary(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "正文")
    resp = _put_output(client, task_id, "正文2", ai_summary="新要点")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["ai_summary"] == "新要点"

    conn = db.connect(config.db_path)
    try:
        assert conn.execute(
            "SELECT ai_summary FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()[0] == "新要点"
    finally:
        conn.close()


# ---- upload ----

def test_upload_stores_original_filename_and_increments(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "v1")

    resp = client.post(
        f"/api/v1/tasks/{task_id}/output/upload",
        auth=AUTH,
        json={"filename": "我的草稿.md", "content": "上传内容"},
    )
    assert resp.status_code == 200, resp.text
    meta = resp.json()["data"]
    assert meta["version"] == 2
    assert meta["filename"] == "我的草稿.md"
    assert _file_path(config, task_id).read_text(encoding="utf-8") == "上传内容"

    body = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH).json()["data"]
    assert body["version"] == 2
    assert body["filename"] == "我的草稿.md"


def test_upload_creates_first_version(tmp_path):
    client = client_for(make_config(tmp_path))
    task_id = _create_task(client)
    resp = client.post(
        f"/api/v1/tasks/{task_id}/output/upload",
        auth=AUTH,
        json={"filename": "init.md", "content": "初始"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["version"] == 1


def test_put_inherits_uploaded_filename(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    client.post(
        f"/api/v1/tasks/{task_id}/output/upload",
        auth=AUTH,
        json={"filename": "draft.md", "content": "草稿"},
    )
    resp = _put_output(client, task_id, "编辑后", base_version=1)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["filename"] == "draft.md"


# ---- version history / export ----

def test_versions_list_metadata_without_content(tmp_path):
    client = client_for(make_config(tmp_path))
    task_id = _create_task(client)
    _put_output(client, task_id, "第一版")
    _put_output(client, task_id, "第二版")

    items = client.get(
        f"/api/v1/tasks/{task_id}/output/versions", auth=AUTH
    ).json()["data"]["items"]
    assert [it["version"] for it in items] == [2, 1]
    for it in items:
        assert "content" not in it
        assert "filename" in it
        assert "created_at" in it


def test_versions_list_no_output_404(tmp_path):
    client = client_for(make_config(tmp_path))
    task_id = _create_task(client)
    assert client.get(
        f"/api/v1/tasks/{task_id}/output/versions", auth=AUTH
    ).status_code == 404


def test_get_version_returns_content(tmp_path):
    client = client_for(make_config(tmp_path))
    task_id = _create_task(client)
    _put_output(client, task_id, "第一版")
    _put_output(client, task_id, "第二版")

    body = client.get(
        f"/api/v1/tasks/{task_id}/output/versions/1", auth=AUTH
    ).json()["data"]
    assert body["content"] == "第一版"
    assert body["version"] == 1
    assert body["filename"] == "output.md"


def test_get_version_missing_404(tmp_path):
    client = client_for(make_config(tmp_path))
    task_id = _create_task(client)
    _put_output(client, task_id, "只有一版")
    assert client.get(
        f"/api/v1/tasks/{task_id}/output/versions/5", auth=AUTH
    ).status_code == 404
    assert client.get(
        f"/api/v1/tasks/{task_id}/output/versions/0", auth=AUTH
    ).status_code == 404


# ---- read-time validation (spec 5.3) ----

def test_external_file_change_backwrites_db_without_version_bump(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "数据库内容")

    # external edit: file content diverges from DB
    _file_path(config, task_id).write_text("外部修改的内容", encoding="utf-8")

    body = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH).json()["data"]
    assert body["content"] == "外部修改的内容"
    # DB backwritten, same version, no version bump
    assert body["version"] == 1
    assert _version_count(config, task_id) == 1
    assert _db_content(config, task_id) == "外部修改的内容"


def test_missing_file_rebuilt_from_db(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "数据库原文")

    _file_path(config, task_id).unlink()
    assert not _file_path(config, task_id).exists()

    body = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH).json()["data"]
    assert body["content"] == "数据库原文"
    # file recreated from DB content
    assert _file_path(config, task_id).read_text(encoding="utf-8") == "数据库原文"


def test_latest_only_file_validated(tmp_path):
    config = make_config(tmp_path)
    client = client_for(config)
    task_id = _create_task(client)
    _put_output(client, task_id, "v1")
    _put_output(client, task_id, "v2")

    # file holds v2; GET returns file content unchanged
    body = client.get(f"/api/v1/tasks/{task_id}/output", auth=AUTH).json()["data"]
    assert body["content"] == "v2"
    assert body["version"] == 2
