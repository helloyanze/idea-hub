import pathlib, sqlite3
import pytest
from idea_hub import db, models

def test_create_and_get_task(conn, target_id):
    tid = models.create_task(conn, title="热点A文章", idea_summary="摘要",
                             target_id=target_id, feasibility_score=7,
                             score_breakdown='{"热度":8,"相关性":7,"可执行性":6}',
                             idea_path="outputs/tasks/1/idea.md")
    task = models.get_task(conn, tid)
    assert task["title"] == "热点A文章"
    assert task["status"] == "todo"  # score 7 >= 6
    assert task["feasibility_score"] == 7

def test_low_score_task_archived(conn, target_id):
    tid = models.create_task(conn, title="低分", idea_summary="s",
                             target_id=target_id, feasibility_score=4,
                             score_breakdown="{}", idea_path="x")
    assert models.get_task(conn, tid)["status"] == "archived"

def test_try_start_task_atomic(conn, target_id):
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=target_id,
                             feasibility_score=7, score_breakdown="{}", idea_path="x")
    models.move_task(conn, tid, "waiting")
    assert models.try_start_task(conn, tid) is True
    assert models.get_task(conn, tid)["status"] == "in_progress"
    assert models.try_start_task(conn, tid) is False  # already in_progress

def test_stats_counts(conn, target_id):
    models.create_task(conn, title="a", idea_summary="s", target_id=target_id,
                       feasibility_score=7, score_breakdown="{}", idea_path="x")
    models.create_task(conn, title="b", idea_summary="s", target_id=target_id,
                       feasibility_score=4, score_breakdown="{}", idea_path="x")
    st = models.stats(conn)
    assert st["todo"] == 1 and st["archived"] == 1

# ---- backup_db（Finding 1：WAL 安全备份 + 剪枝） ----

def test_backup_db_includes_recent_commits(conn, target_id, tmp_path):
    """WAL 模式下备份必须包含未 checkpoint 的最近提交（回归：shutil.copy2 会静默丢失）。"""
    tid = models.create_task(conn, title="备份完整性", idea_summary="s", target_id=target_id,
                             feasibility_score=7, score_breakdown="{}", idea_path="x")
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
                             feasibility_score=7, score_breakdown="{}", idea_path="x")
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
                             feasibility_score=7, score_breakdown="{}", idea_path="x")
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
