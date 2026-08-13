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
    keywords TEXT NOT NULL DEFAULT '',
    tier TEXT NOT NULL DEFAULT 'D'
);
CREATE TABLE IF NOT EXISTS hot_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    content_snapshot TEXT NOT NULL DEFAULT '',
    collected_at TEXT NOT NULL DEFAULT (datetime('now')),
    source_score REAL NOT NULL DEFAULT 0,
    fact_score REAL NOT NULL DEFAULT 0,
    verify_score REAL NOT NULL DEFAULT 60,
    time_score REAL NOT NULL DEFAULT 100,
    final_score REAL NOT NULL DEFAULT 0,
    review_status TEXT NOT NULL DEFAULT 'collected',
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
    if "tier" not in cols:
        conn.execute("ALTER TABLE sources ADD COLUMN tier TEXT NOT NULL DEFAULT 'D'")
    # sources.type CHECK 约束扩展（SQLite 无法 ALTER CHECK，需重建表）
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'").fetchone()
    if sql and "github-trending" not in (sql[0] or ""):
        conn.execute("PRAGMA foreign_keys=OFF")
        # legacy_alter_table=ON：RENAME 时不重写其他表对 sources 的外键引用，
        # 否则 hot_items.source_id 会被改写为 sources_old 导致悬空引用
        conn.execute("PRAGMA legacy_alter_table=ON")
        conn.execute("ALTER TABLE sources RENAME TO sources_old")
        conn.execute("""CREATE TABLE sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK (type IN ('hotlist','rss','github-trending','hackernews')),
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            items_path TEXT NOT NULL DEFAULT 'data',
            title_field TEXT NOT NULL DEFAULT 'title',
            keywords TEXT NOT NULL DEFAULT '',
            tier TEXT NOT NULL DEFAULT 'D'
        )""")
        conn.execute("""INSERT INTO sources (id, type, name, url, enabled, items_path, title_field, keywords)
                        SELECT id, type, name, url, enabled, items_path, title_field, IFNULL(keywords, '')
                        FROM sources_old""")
        conn.execute("DROP TABLE sources_old")
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")
    # hot_items 评分列（评分机制 v1）
    hcols = {r["name"] for r in conn.execute("PRAGMA table_info(hot_items)").fetchall()}
    for col, dflt in (("source_score", "0"), ("fact_score", "0"), ("verify_score", "60"),
                      ("time_score", "100"), ("final_score", "0"),
                      ("review_status", "'collected'")):
        if col not in hcols:
            conn.execute(f"ALTER TABLE hot_items ADD COLUMN {col} REAL NOT NULL DEFAULT {dflt}"
                         if col != "review_status" else
                         f"ALTER TABLE hot_items ADD COLUMN {col} TEXT NOT NULL DEFAULT {dflt}")
    # tasks.content_type 旧列（content_types 方案残留）——v2 起 content_type 成为正式字段，
    # 不再删除（旧 DROP 逻辑会与 _migrate_v2 新增列冲突，导致每次启动重置 content_type）
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
    _migrate_v2(conn)  # v2：自动执行调度字段 + notifications 表 + expire_at 回填（幂等）

def _migrate_v2(conn: sqlite3.Connection) -> None:
    """v2: 自动执行调度（content_type/is_complex/fail_count/expire_at/token_used/redo_note,
    sources.ttl_hours, settings 调度配置, notifications 表, expire_at 回填）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "content_type" not in cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN content_type TEXT DEFAULT 'long'")
        conn.execute("ALTER TABLE tasks ADD COLUMN is_complex INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE tasks ADD COLUMN fail_count INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE tasks ADD COLUMN last_fail_reason TEXT")
        conn.execute("ALTER TABLE tasks ADD COLUMN expire_at TEXT")
        conn.execute("ALTER TABLE tasks ADD COLUMN token_used INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE tasks ADD COLUMN redo_note TEXT")
    scol = {r["name"] for r in conn.execute("PRAGMA table_info(sources)").fetchall()}
    if "ttl_hours" not in scol:
        conn.execute("ALTER TABLE sources ADD COLUMN ttl_hours INTEGER DEFAULT 24")
    defaults = {
        "auto_execute": "1", "max_concurrent": "1", "max_fail_count": "3",
        "stale_simple_min": "5", "stale_complex_min": "60",
        "max_daily_tokens": "500000", "last_scheduler_tick": "",
        "ai_taste_blacklist": "首先,其次,最后,总的来说,值得注意的是,综上所述,众所周知,不言而喻,赋能,抓手,闭环",
    }
    for k, v in defaults.items():
        if conn.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone() is None:
            conn.execute("INSERT INTO settings (key, value) VALUES (?,?)", (k, v))
    conn.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER, type TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
        is_read INTEGER DEFAULT 0, created_at TEXT NOT NULL)""")
    # expire_at 回填：有关联热点且热点有 ttl_hours
    # 注：sqlite datetime() 输出 'YYYY-MM-DD HH:MM:SS'（空格分隔），统一替换为 ISO 'T' 分隔，
    # 与 create_task 写入的 expire_at 格式保持一致（测试断言 '2026-08-14T00:00:00'）
    conn.execute("""UPDATE tasks SET expire_at = (
            SELECT replace(datetime(h.collected_at, '+' || s.ttl_hours || ' hours'), ' ', 'T')
            FROM hot_items h JOIN sources s ON s.id = h.source_id
            WHERE h.id = tasks.hot_item_id AND s.ttl_hours IS NOT NULL)
        WHERE expire_at IS NULL AND hot_item_id IS NOT NULL""")
    conn.commit()

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
