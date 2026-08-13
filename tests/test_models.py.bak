import pathlib, sqlite3
import pytest
from idea_hub import db, models

def test_create_and_get_task(conn, target_id):
    tid = models.create_task(conn, title="热点A文章", idea_summary="摘要",
                             target_id=target_id, feasibility_score=8,
                             score_breakdown='{"热度":8,"相关性":7,"可执行性":6}',
                             idea_path="outputs/tasks/1/idea.md")
    task = models.get_task(conn, tid)
    assert task["title"] == "热点A文章"
    assert task["status"] == "todo"  # score 8 >= 8
    assert task["feasibility_score"] == 8

def test_low_score_task_archived(conn, target_id):
    tid = models.create_task(conn, title="低分", idea_summary="s",
                             target_id=target_id, feasibility_score=6,
                             score_breakdown="{}", idea_path="x")
    assert models.get_task(conn, tid)["status"] == "archived"  # 6-7 归档
    assert models.create_task(conn, title="舍弃", idea_summary="s",
                              target_id=target_id, feasibility_score=5,
                              score_breakdown="{}", idea_path="x") is None  # <6 舍弃

def test_try_start_task_atomic(conn, target_id):
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=target_id,
                             feasibility_score=7, score_breakdown="{}", idea_path="x")
    models.move_task(conn, tid, "waiting")
    assert models.try_start_task(conn, tid) is True
    assert models.get_task(conn, tid)["status"] == "in_progress"
    assert models.try_start_task(conn, tid) is False  # already in_progress

def test_stats_counts(conn, target_id):
    models.create_task(conn, title="a", idea_summary="s", target_id=target_id,
                       feasibility_score=8, score_breakdown="{}", idea_path="x")
    models.create_task(conn, title="b", idea_summary="s", target_id=target_id,
                       feasibility_score=6, score_breakdown="{}", idea_path="x")
    st = models.stats(conn)
    assert st["todo"] == 1 and st["archived"] == 1

# ---- backup_db（Finding 1：WAL 安全备份 + 剪枝） ----

def test_backup_db_includes_recent_commits(conn, target_id, tmp_path):
    """WAL 模式下备份必须包含未 checkpoint 的最近提交（回归：shutil.copy2 会静默丢失）。"""
    tid = models.create_task(conn, title="备份完整性", idea_summary="s", target_id=target_id,
                             feasibility_score=8, score_breakdown="{}", idea_path="x")
    dest = db.backup_db(conn, str(tmp_path / "backups"))
    assert pathlib.Path(dest).exists()
    bconn = sqlite3.connect(dest)
    try:
        row = bconn.execute("SELECT title, status FROM tasks WHERE id=?", (tid,)).fetchone()
        assert row is not None
        assert row[0] == "备份完整性" and row[1] == "todo"
    finally:
        bconn.close()


def test_backup_db_prunes_to_seven(conn, target_id, tmp_path):
    backups_dir = str(tmp_path / "backups")
    db.backup_db(conn, backups_dir)
    for i in range(9):  # 塞入 9 份过期备份（文件名各不相同，避免同秒覆盖）
        pathlib.Path(backups_dir, f"idea-20260101-{i:06d}.db").touch()
    dest = db.backup_db(conn, backups_dir)  # 触发剪枝
    remaining = sorted(pathlib.Path(backups_dir).glob("idea-*.db"))
    assert len(remaining) == 7
    assert pathlib.Path(dest) in remaining  # 最新备份被保留

# ---- update_task（Finding 2：status 值校验） ----

def test_update_task_rejects_invalid_status(conn, target_id):
    """status 不属于 update_task 可写字段——无论合法/非法值都必须抛 KeyError（状态变更只能走 move_task）。"""
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=target_id,
                             feasibility_score=8, score_breakdown="{}", idea_path="x")
    with pytest.raises(KeyError):
        models.update_task(conn, tid, status="done!!")
    with pytest.raises(KeyError):
        models.update_task(conn, tid, status="waiting")  # 合法值同样被拒绝
    # 状态未被改动，stats() 正常
    assert models.get_task(conn, tid)["status"] == "todo"
    assert models.stats(conn)["todo"] == 1


def test_update_task_normal_fields_unaffected(conn, target_id):
    """非 status 字段（notes/title/feasibility_score）更新正常；status 字段被拒绝，只能走 move_task。"""
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=target_id,
                             feasibility_score=8, score_breakdown="{}", idea_path="x")
    models.update_task(conn, tid, notes="reviewed", title="新标题", feasibility_score=8)
    task = models.get_task(conn, tid)
    assert task["notes"] == "reviewed" and task["title"] == "新标题"
    assert task["feasibility_score"] == 8 and task["status"] == "todo"
    # status 不再可写；状态变更必须走 move_task
    with pytest.raises(KeyError):
        models.update_task(conn, tid, status="waiting")
    models.move_task(conn, tid, "waiting")
    assert models.get_task(conn, tid)["status"] == "waiting"

# ---- Task 2: targets / sources / settings CRUD ----

def test_activate_target_singleton(conn, target_id):
    t2 = models.create_target(conn, name="开发类", description="d",
                              score_dimensions='{"技术":1}')
    models.activate_target(conn, t2)
    assert models.get_active_target(conn)["id"] == t2
    models.activate_target(conn, target_id)
    assert models.get_active_target(conn)["id"] == target_id

def test_source_crud(conn):
    sid = models.create_source(conn, type="hotlist", name="微博热搜", url="https://x/api")
    assert models.list_sources(conn, enabled_only=True)[0]["name"] == "微博热搜"
    models.set_source_enabled(conn, sid, False)
    assert models.list_sources(conn, enabled_only=True) == []
    assert len(models.list_sources(conn)) == 1

def test_settings(conn):
    assert models.get_setting(conn, "threshold") is None
    models.set_setting(conn, "threshold", "6")
    assert models.get_setting(conn, "threshold") == "6"

# ---- S3: PRAGMA foreign_keys=ON（connect() 已启用） ----

def test_foreign_keys_enforced(conn):
    """hot_items.source_id 引用不存在的 sources.id 必须抛 IntegrityError（回归：FK 未启用时静默插入）。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (999, 'x', 'http://x')")

def test_create_source_with_custom_fields(conn):
    sid = models.create_source(conn, type="hotlist", name="自定义", url="http://x",
                               items_path="data.list", title_field="name")
    src = models.list_sources(conn)[-1]
    assert src["items_path"] == "data.list"
    assert src["title_field"] == "name"

def test_create_source_defaults_backward_compatible(conn):
    sid = models.create_source(conn, type="rss", name="默认", url="http://y")
    src = models.list_sources(conn)[-1]
    assert src["items_path"] == "data"
    assert src["title_field"] == "title"

# ---- tags 测试 ----

def test_tags_crud_and_task_tags(conn, target_id):
    tid = models.create_tag(conn, name="langchain", description="LangChain 框架")
    assert tid > 0
    assert models.list_tags(conn)[-1]["name"] == "langchain"
    models.set_tag_active(conn, tid, False)
    assert all(t["name"] != "langchain" for t in models.list_tags(conn, active_only=True))
    task = models.create_task(conn, title="t", idea_summary="s", target_id=target_id,
                              feasibility_score=7, score_breakdown="{}", idea_path="x")
    models.add_task_tag(conn, task, tid)
    tags = models.list_task_tags(conn, task)
    assert tags[0]["name"] == "langchain"
    models.delete_tag(conn, tid)
    assert models.list_task_tags(conn, task) == []
    assert all(t["name"] != "langchain" for t in models.list_tags(conn))

def test_default_tags_seeded(conn):
    names = {t["name"] for t in models.list_tags(conn)}
    assert "AI" in names and "Agent" in names

def test_migration_content_types_to_tags(tmp_path):
    """旧 content_types 表迁移为 tags。"""
    import sqlite3 as _sqlite3
    db_path = tmp_path / "old2.db"
    c = _sqlite3.connect(str(db_path))
    c.execute("CREATE TABLE content_types (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1)")
    c.execute("INSERT INTO content_types (name, description) VALUES ('article','文章')")
    c.commit(); c.close()
    conn = db.connect(str(db_path))
    db.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] >= 1
    names = {r["name"] for r in conn.execute("SELECT name FROM tags").fetchall()}
    assert "article" in names
    conn.close()

def test_migration_rebuild_preserves_fk(conn, tmp_path):
    """sources 表重建后 hot_items 外键必须指向新 sources 表（回归：RENAME 悬空引用）。"""
    import sqlite3 as _sqlite3
    db_path = tmp_path / "fk.db"
    c = _sqlite3.connect(str(db_path))
    c.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "type TEXT NOT NULL CHECK (type IN ('hotlist','rss')), name TEXT NOT NULL, "
              "url TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1)")
    c.execute("CREATE TABLE hot_items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "source_id INTEGER NOT NULL REFERENCES sources(id), title TEXT NOT NULL, "
              "url TEXT NOT NULL, content_snapshot TEXT NOT NULL DEFAULT '', "
              "collected_at TEXT NOT NULL DEFAULT (datetime('now')))")
    c.execute("INSERT INTO sources (type, name, url) VALUES ('rss','旧源','http://x')")
    c.commit(); c.close()
    conn = db.connect(str(db_path))
    db.init_schema(conn)  # 触发重建
    # 外键完好：插入 hot_item 引用 sources 正常
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, 'T', 'http://t')")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM hot_items").fetchone()[0] == 1
    conn.close()
