import sqlite3

STATUSES = ("archived", "todo", "waiting", "in_progress", "done")
SCORE_THRESHOLD = 6

def create_task(conn, *, title, idea_summary, target_id, hot_item_id=None,
                feasibility_score, score_breakdown, idea_path, notes=""):
    status = "todo" if feasibility_score >= SCORE_THRESHOLD else "archived"
    cur = conn.execute(
        "INSERT INTO tasks (title, idea_summary, idea_path, hot_item_id, target_id, status, feasibility_score, score_breakdown, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (title, idea_summary, idea_path, hot_item_id, target_id, status,
         feasibility_score, score_breakdown, notes))
    conn.commit()
    return cur.lastrowid

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
    allowed = {"title", "idea_summary", "feasibility_score", "score_breakdown",
               "ai_summary", "output_path", "notes"}
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

def create_source(conn, *, type, name, url, enabled=True):
    cur = conn.execute("INSERT INTO sources (type, name, url, enabled) VALUES (?,?,?,?)",
                       (type, name, url, 1 if enabled else 0))
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

def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
    conn.commit()
