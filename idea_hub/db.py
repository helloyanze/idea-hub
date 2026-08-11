import sqlite3, pathlib, shutil
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    score_dimensions TEXT NOT NULL DEFAULT '{}',
    is_active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('hotlist','rss')),
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS hot_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    content_snapshot TEXT NOT NULL DEFAULT '',
    collected_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source_id, url)
);
CREATE TABLE IF NOT EXISTS task_links (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    hot_item_id INTEGER NOT NULL REFERENCES hot_items(id),
    PRIMARY KEY (task_id, hot_item_id)
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    idea_summary TEXT NOT NULL DEFAULT '',
    idea_path TEXT NOT NULL DEFAULT '',
    hot_item_id INTEGER REFERENCES hot_items(id),
    target_id INTEGER NOT NULL REFERENCES targets(id),
    status TEXT NOT NULL DEFAULT 'todo',
    feasibility_score INTEGER NOT NULL,
    score_breakdown TEXT NOT NULL DEFAULT '{}',
    ai_summary TEXT NOT NULL DEFAULT '',
    output_path TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execute_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()

def backup_db(conn: sqlite3.Connection, backups_dir: str) -> str:
    pathlib.Path(backups_dir).mkdir(parents=True, exist_ok=True)
    db_path = pathlib.Path(conn.execute("PRAGMA database_list").fetchone()["file"])
    dest = pathlib.Path(backups_dir) / f"idea-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(db_path, dest)
    copies = sorted(pathlib.Path(backups_dir).glob("idea-*.db"), reverse=True)
    for old in copies[7:]:
        old.unlink()
    return str(dest)
