import sqlite3, pathlib
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
    enabled INTEGER NOT NULL DEFAULT 1,
    items_path TEXT NOT NULL DEFAULT 'data',
    title_field TEXT NOT NULL DEFAULT 'title'
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
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()

def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：为已有数据库补充新增列（CREATE TABLE IF NOT EXISTS 不处理已存在表）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    if "items_path" not in cols:
        conn.execute("ALTER TABLE sources ADD COLUMN items_path TEXT NOT NULL DEFAULT 'data'")
    if "title_field" not in cols:
        conn.execute("ALTER TABLE sources ADD COLUMN title_field TEXT NOT NULL DEFAULT 'title'")

def backup_db(conn: sqlite3.Connection, backups_dir: str) -> str:
    pathlib.Path(backups_dir).mkdir(parents=True, exist_ok=True)
    dest = pathlib.Path(backups_dir) / f"idea-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    # 用 sqlite3 内建备份 API：WAL 模式下会一并读取 -wal 中未 checkpoint 的帧，
    # 而 shutil.copy2 直接复制主库文件只能得到"截至上次 checkpoint"的过期快照（静默丢失最近提交）。
    # 注：Python 3.11 的 backup() target 只接受 sqlite3.Connection，需先打开目标连接。
    with sqlite3.connect(str(dest)) as bconn:
        conn.backup(bconn)
    copies = sorted(pathlib.Path(backups_dir).glob("idea-*.db"), reverse=True)
    for old in copies[7:]:
        old.unlink()
    return str(dest)
