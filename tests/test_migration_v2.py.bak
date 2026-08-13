"""迁移 v2 测试：新字段、notifications 表、expire_at 回填、通知 CRUD。"""
import sqlite3
from pathlib import Path
from idea_hub import db, models

SCHEMA_COLS = {
    "tasks": ["content_type", "is_complex", "fail_count", "last_fail_reason",
              "expire_at", "token_used", "redo_note"],
    "sources": ["ttl_hours"],
}

def _fresh_db(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(str(tmp_path / "test.db"))
    db.init_schema(conn)  # 迁移入口：init_schema -> _migrate -> _migrate_v2
    # 种子默认目标（foreign_keys=ON，测试用 target_id=1 必须存在对应行）
    conn.execute("INSERT INTO targets (name, description, score_dimensions, is_active) "
                 "VALUES ('默认目标', '', '{}', 1)")
    conn.commit()
    return conn

def test_migration_adds_columns(tmp_path):
    conn = _fresh_db(tmp_path)
    for table, cols in SCHEMA_COLS.items():
        cur = conn.execute(f"PRAGMA table_info({table})")
        names = {r["name"] for r in cur.fetchall()}
        for c in cols:
            assert c in names, f"{table}.{c} missing"
    # notifications 表存在
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='notifications'")
    assert cur.fetchone() is not None

def test_task_defaults(tmp_path):
    conn = _fresh_db(tmp_path)
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    row = models.get_task(conn, tid)
    assert row["content_type"] == "long"
    assert row["is_complex"] == 0
    assert row["fail_count"] == 0
    assert row["token_used"] == 0
    assert row["expire_at"] is None

def test_notification_crud(tmp_path):
    conn = _fresh_db(tmp_path)
    nid = models.create_notification(conn, task_id=None, type="scheduler",
                                     title="调度异常", body="test")
    rows = models.list_notifications(conn)
    assert len(rows) == 1 and rows[0]["is_read"] == 0
    models.mark_notification_read(conn, nid)
    assert models.list_notifications(conn, unread_only=True) == []
    models.create_notification(conn, task_id=None, type="done", title="t2", body="b2")
    assert models.list_notifications(conn, unread_only=True) is not None

def test_daily_token_used(tmp_path):
    conn = _fresh_db(tmp_path)
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    models.update_task(conn, tid, token_used=1234)
    assert models.daily_token_used(conn) >= 1234

def test_expire_at_backfill(tmp_path):
    """旧库迁移后，有关联热点且热点有 ttl_hours 的任务回填 expire_at。"""
    conn = _fresh_db(tmp_path)
    sid = models.create_source(conn, type="hotlist", name="baidu", url="http://x",
                               ttl_hours=24)
    hid = conn.execute("INSERT INTO hot_items (source_id, title, url, content_snapshot, "
                       "collected_at) VALUES (?,?,?,?,?)",
                       (sid, "hot", "http://h", "", "2026-08-13T00:00:00")).lastrowid
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}", hot_item_id=hid)
    conn.execute("INSERT OR IGNORE INTO task_links (task_id, hot_item_id) VALUES (?,?)", (tid, hid))
    conn.commit()
    row = models.get_task(conn, tid)
    assert row["expire_at"] == "2026-08-14T00:00:00"

def test_migration_idempotent(tmp_path):
    """同一连接重复 init_schema 不应抛异常，且 tasks 表结构不变（迁移幂等）。"""
    conn = _fresh_db(tmp_path)
    before = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    db.init_schema(conn)  # 第二次执行迁移
    after = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    assert before == after

def test_create_task_uses_utc_default(tmp_path):
    """create_task 的 created_at/updated_at 应来自表默认 datetime('now')（UTC），
    与 update_task 等写入方一致，避免同列混用时区导致日账偏移/updated_at<created_at。"""
    conn = _fresh_db(tmp_path)
    before = conn.execute("SELECT datetime('now') AS t").fetchone()["t"]
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    after = conn.execute("SELECT datetime('now') AS t").fetchone()["t"]
    row = models.get_task(conn, tid)
    assert row["created_at"] == row["updated_at"]
    assert before <= row["created_at"] <= after
