import sqlite3
from datetime import datetime, timezone

STATUSES = ("archived", "todo", "waiting", "in_progress", "done")
SCORE_THRESHOLD = 6          # 历史兼容：旧任务/relate 升级用（>=6 可升待办）
SCORE_TODO = 8               # 新阈值：>=8 待办
SCORE_ARCHIVE = 6            # 6-7 归档；<6 舍弃

def create_task(conn, *, title, idea_summary, target_id, hot_item_id=None,
                feasibility_score, score_breakdown, idea_path="", notes="",
                content_type="long", expire_at=None):
    """按新阈值分流：>=8 todo / 6-7 archived / <6 返回 None（舍弃，不创建）。

    v2：未显式给 expire_at 且关联热点（其来源配置了 ttl_hours）时，
    按 collected_at + ttl_hours 自动计算，规则与迁移回填一致。
    """
    if feasibility_score < SCORE_ARCHIVE:
        return None
    status = "todo" if feasibility_score >= SCORE_TODO else "archived"
    if expire_at is None and hot_item_id is not None:
        row = conn.execute(
            "SELECT datetime(h.collected_at, '+' || s.ttl_hours || ' hours') AS e "
            "FROM hot_items h JOIN sources s ON s.id = h.source_id "
            "WHERE h.id=? AND s.ttl_hours IS NOT NULL", (hot_item_id,)).fetchone()
        if row and row["e"]:
            expire_at = row["e"]
    cur = conn.execute(
        "INSERT INTO tasks (title, idea_summary, idea_path, hot_item_id, target_id, status, "
        "feasibility_score, score_breakdown, notes, content_type, expire_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (title, idea_summary, idea_path, hot_item_id, target_id, status,
         feasibility_score, score_breakdown, notes, content_type, expire_at))
    tid = cur.lastrowid
    if hot_item_id is not None:
        conn.execute("INSERT OR IGNORE INTO task_links (task_id, hot_item_id) VALUES (?,?)",
                     (tid, hot_item_id))
    conn.commit()
    return tid

def get_task(conn, task_id):
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return dict(row) if row else None

def list_tasks(conn, status=None, target_id=None):
    sql, args = "SELECT * FROM tasks", []
    where = []
    if status: where.append("status=?"); args.append(status)
    if target_id is not None: where.append("target_id=?"); args.append(target_id)
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]

def update_task(conn, task_id, **fields):
    allowed = {"title", "idea_summary", "idea_path", "feasibility_score", "score_breakdown",
               "ai_summary", "output_path", "notes", "content_type", "is_complex",
               "fail_count", "last_fail_reason", "expire_at", "token_used", "redo_note"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed: raise KeyError(f"unknown field {k}")
        sets.append(f"{k}=?"); args.append(v)
    if not sets: return
    args.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(sets)}, updated_at=datetime('now') WHERE id=?", args)
    conn.commit()

def try_start_task(conn, task_id):
    cur = conn.execute("UPDATE tasks SET status='in_progress', updated_at=datetime('now') "
                       "WHERE id=? AND status='waiting'", (task_id,))
    conn.commit()
    return cur.rowcount == 1

def move_task(conn, task_id, to_status):
    if to_status not in STATUSES: raise ValueError(f"bad status {to_status}")
    completed_at = "datetime('now')" if to_status == "done" else "NULL"
    conn.execute(f"UPDATE tasks SET status=?, completed_at={completed_at}, updated_at=datetime('now') WHERE id=?", (to_status, task_id))
    conn.commit()

def stats(conn, target_id=None):
    sql = "SELECT status, COUNT(*) n FROM tasks"
    args = []
    if target_id is not None:
        sql += " WHERE target_id=?"; args.append(target_id)
    sql += " GROUP BY status"
    out = {s: 0 for s in STATUSES}
    for r in conn.execute(sql, args).fetchall():
        out[r["status"]] = r["n"]
    return out

# ---- Task 2: targets / sources / settings CRUD ----

def create_target(conn, *, name, description, score_dimensions):
    cur = conn.execute("INSERT INTO targets (name, description, score_dimensions) VALUES (?,?,?)",
                       (name, description, score_dimensions))
    conn.commit()
    return cur.lastrowid

def activate_target(conn, target_id):
    conn.execute("UPDATE targets SET is_active=0")
    conn.execute("UPDATE targets SET is_active=1 WHERE id=?", (target_id,))
    conn.commit()

def get_active_target(conn):
    row = conn.execute("SELECT * FROM targets WHERE is_active=1").fetchone()
    return dict(row) if row else None

def list_targets(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM targets ORDER BY id").fetchall()]

def create_source(conn, *, type, name, url, enabled=True, items_path="data", title_field="title", keywords="", ttl_hours=24):
    cur = conn.execute("INSERT INTO sources (type, name, url, enabled, items_path, title_field, keywords, ttl_hours) VALUES (?,?,?,?,?,?,?,?)",
                       (type, name, url, 1 if enabled else 0, items_path, title_field, keywords, ttl_hours))
    conn.commit()
    return cur.lastrowid

def list_sources(conn, enabled_only=False):
    sql = "SELECT * FROM sources"
    if enabled_only: sql += " WHERE enabled=1"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql).fetchall()]

def set_source_enabled(conn, source_id, enabled):
    conn.execute("UPDATE sources SET enabled=? WHERE id=?", (1 if enabled else 0, source_id))
    conn.commit()

_SOURCE_FIELDS = ("type", "name", "url", "enabled", "items_path", "title_field",
                  "keywords", "ttl_hours")

def update_source(conn, source_id, **fields):
    """部分更新来源字段：白名单字段，None 值跳过（不覆盖）。返回受影响行数。"""
    try:
        exists = conn.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone()
    except sqlite3.OperationalError:
        exists = None
    if not exists:
        raise ValueError(f"source {source_id} not found")
    sets, args = [], []
    for k in _SOURCE_FIELDS:
        if k in fields and fields[k] is not None:
            v = fields[k]
            if k == "enabled":
                v = 1 if v else 0
            sets.append(f"{k}=?")
            args.append(v)
    if sets:
        conn.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id=?", (*args, source_id))
        conn.commit()

def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()

# ---- tags（主题标签，可自定义）----

def create_tag(conn, *, name, description=""):
    """创建标签；同名已存在时直接返回已有 id（防重）。"""
    existing = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute("INSERT INTO tags (name, description, is_active) VALUES (?,?,1)",
                       (name, description))
    conn.commit()
    return cur.lastrowid

def list_tags(conn, active_only=False):
    sql = "SELECT * FROM tags"
    if active_only:
        sql += " WHERE is_active=1"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql).fetchall()]

def set_tag_active(conn, tag_id, active):
    conn.execute("UPDATE tags SET is_active=? WHERE id=?", (1 if active else 0, tag_id))
    conn.commit()

def delete_tag(conn, tag_id):
    conn.execute("DELETE FROM task_tags WHERE tag_id=?", (tag_id,))
    conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    conn.commit()

def add_task_tag(conn, task_id, tag_id):
    conn.execute("INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?,?)", (task_id, tag_id))
    conn.commit()

def list_task_tags(conn, task_id):
    rows = conn.execute("SELECT t.id, t.name FROM task_tags tt JOIN tags t ON t.id=tt.tag_id "
                        "WHERE tt.task_id=? ORDER BY t.id", (task_id,)).fetchall()
    return [dict(r) for r in rows]

# ---- 自动执行调度 v2：notifications / token 统计 / 健康检查 ----

def create_notification(conn, *, task_id, type, title, body):
    cur = conn.execute(
        "INSERT INTO notifications (task_id, type, title, body, created_at) VALUES (?,?,?,?,?)",
        (task_id, type, title, body, datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return cur.lastrowid

def list_notifications(conn, unread_only=False, limit=50):
    sql = "SELECT * FROM notifications"
    if unread_only:
        sql += " WHERE is_read=0"
    sql += " ORDER BY id DESC LIMIT ?"
    return [dict(r) for r in conn.execute(sql, (limit,)).fetchall()]

def mark_notification_read(conn, notification_id):
    conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (notification_id,))
    conn.commit()

def mark_all_notifications_read(conn):
    conn.execute("UPDATE notifications SET is_read=1")
    conn.commit()

def clear_old_notifications(conn, days=30):
    cur = conn.execute("DELETE FROM notifications WHERE created_at < datetime('now', ?)",
                       (f"-{days} days",))
    conn.commit()
    return cur.rowcount

def daily_token_used(conn, date_str=None):
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COALESCE(SUM(token_used),0) AS t FROM tasks WHERE date(updated_at)=?",
        (date_str,)).fetchone()
    return row["t"]

def get_health(conn):
    """调度器健康信息：last_scheduler_tick + 距今分钟数。"""
    ts = get_setting(conn, "last_scheduler_tick", "")
    minutes = None
    if ts:
        try:
            last = datetime.fromisoformat(ts)
            minutes = int((datetime.now() - last).total_seconds() // 60)
        except ValueError:
            minutes = None
    return {"last_tick": ts, "minutes_ago": minutes}
