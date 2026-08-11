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
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS task_tags (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (task_id, tag_id)
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('hotlist','rss','github-trending','hackernews')),
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    items_path TEXT NOT NULL DEFAULT 'data',
    title_field TEXT NOT NULL DEFAULT 'title',
    keywords TEXT NOT NULL DEFAULT ''
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
    """轻量迁移：为已有数据库补充新增表/列/约束（CREATE TABLE IF NOT EXISTS 不处理已存在对象）。"""
    # sources 新列
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    if "items_path" not in cols:
        conn.execute("ALTER TABLE sources ADD COLUMN items_path TEXT NOT NULL DEFAULT 'data'")
    if "title_field" not in cols:
        conn.execute("ALTER TABLE sources ADD COLUMN title_field TEXT NOT NULL DEFAULT 'title'")
    if "keywords" not in cols:
        conn.execute("ALTER TABLE sources ADD COLUMN keywords TEXT NOT NULL DEFAULT ''")
    # sources.type CHECK 约束扩展（SQLite 无法 ALTER CHECK，需重建表）
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'").fetchone()
    if sql and "github-trending" not in (sql[0] or ""):
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("ALTER TABLE sources RENAME TO sources_old")
        conn.execute("""CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK (type IN ('hotlist','rss','github-trending','hackernews')),
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            items_path TEXT NOT NULL DEFAULT 'data',
            title_field TEXT NOT NULL DEFAULT 'title',
            keywords TEXT NOT NULL DEFAULT ''
        )""")
        conn.execute("""INSERT INTO sources (id, type, name, url, enabled, items_path, title_field, keywords)
                        SELECT id, type, name, url, enabled, items_path, title_field, IFNULL(keywords, '')
                        FROM sources_old""")
        conn.execute("DROP TABLE sources_old")
        conn.execute("PRAGMA foreign_keys=ON")
    # tasks.content_type 旧列（content_types 方案残留）——若存在则删除
    tcols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "content_type" in tcols:
        try:
            conn.execute("ALTER TABLE tasks DROP COLUMN content_type")
        except sqlite3.OperationalError:
            pass  # 旧版 SQLite 不支持 DROP COLUMN，保留无害
    # 旧 content_types 表（早期方案残留）——若存在则重命名为 tags 并转移数据
    has_ct = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='content_types'").fetchone()[0]
    if has_ct:
        conn.execute("ALTER TABLE content_types RENAME TO tags_old")
        conn.execute("CREATE TABLE IF NOT EXISTS tags ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
                     "description TEXT NOT NULL DEFAULT '', is_active INTEGER NOT NULL DEFAULT 1)")
        conn.execute("INSERT OR IGNORE INTO tags (id, name, description, is_active) "
                     "SELECT id, name, description, is_active FROM tags_old")
        conn.execute("DROP TABLE tags_old")
    # tags 默认数据（仅当表为空时）
    if conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0:
        conn.executemany("INSERT INTO tags (name, description) VALUES (?,?)", [
            ("AI", "人工智能相关"),
            ("Agent", "智能体/自主代理"),
            ("新技术", "新兴技术与趋势"),
            ("工具", "实用工具与产品"),
            ("行业观察", "行业动态与商业分析"),
        ])

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
