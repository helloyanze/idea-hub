import json
import os
import sqlite3


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('hotlist','rss','github-trending','hackernews')),
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    items_path TEXT NOT NULL DEFAULT 'data',
    title_field TEXT NOT NULL DEFAULT 'title',
    keywords TEXT NOT NULL DEFAULT '',
    ttl_hours INTEGER DEFAULT 24,
    channel_config TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS hot_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    content_snapshot TEXT NOT NULL DEFAULT '',
    collected_at TEXT NOT NULL DEFAULT (datetime('now')),
    final_score REAL NOT NULL DEFAULT 0,
    score_breakdown TEXT NOT NULL DEFAULT '{}',
    verdict TEXT NOT NULL DEFAULT 'admit' CHECK (verdict IN ('admit','discard')),
    collected_date TEXT NOT NULL DEFAULT (date('now')),
    UNIQUE (source_id, url)
);

CREATE INDEX IF NOT EXISTS idx_hot_items_verdict_date ON hot_items(verdict, collected_date);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    idea_summary TEXT NOT NULL DEFAULT '',
    ai_summary TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT 'article' CHECK (content_type IN ('article','video_script','tweet','newsletter')),
    status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo','waiting','in_progress','done')),
    feasibility_score INTEGER NOT NULL DEFAULT 0,
    score_breakdown TEXT NOT NULL DEFAULT '{}',
    target_desc TEXT NOT NULL DEFAULT '',
    expire_at TEXT,
    token_used INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_fail_reason TEXT,
    redo_note TEXT,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS task_links (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    hot_item_id INTEGER NOT NULL REFERENCES hot_items(id),
    PRIMARY KEY (task_id, hot_item_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL DEFAULT '#3b82f6'
);

CREATE TABLE IF NOT EXISTS task_tags (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    tag_id INTEGER NOT NULL REFERENCES tags(id),
    PRIMARY KEY (task_id, tag_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    value_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('collect_done','generate_done','execute_done','job_failed','task_expired','budget_exceeded','discard_cleaned')),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'info' CHECK (level IN ('info','warn','error')),
    entity_type TEXT,
    entity_id INTEGER,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    version INTEGER NOT NULL,
    filename TEXT NOT NULL DEFAULT 'output.md',
    content TEXT NOT NULL DEFAULT '',
    file_mtime TEXT,
    file_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (task_id, version)
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK (type IN ('collect','generate','execute')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','done','failed')),
    progress INTEGER NOT NULL DEFAULT 0,
    result_ref TEXT,
    error TEXT,
    heartbeat_at TEXT,
    token_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS hot_items_fts USING fts5(
    title,
    content_snapshot,
    content='hot_items',
    content_rowid='id',
    tokenize='trigram'
);

CREATE VIRTUAL TABLE IF NOT EXISTS tasks_fts USING fts5(
    title,
    idea_summary,
    ai_summary,
    content='tasks',
    content_rowid='id',
    tokenize='trigram'
);

CREATE VIRTUAL TABLE IF NOT EXISTS outputs_fts USING fts5(
    content,
    task_id UNINDEXED,
    content='outputs',
    content_rowid='id',
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS hot_items_ai AFTER INSERT ON hot_items BEGIN
    INSERT INTO hot_items_fts(rowid, title, content_snapshot)
    VALUES (NEW.id, NEW.title, NEW.content_snapshot);
END;

CREATE TRIGGER IF NOT EXISTS hot_items_au AFTER UPDATE ON hot_items BEGIN
    INSERT INTO hot_items_fts(hot_items_fts, rowid, title, content_snapshot)
    VALUES ('delete', OLD.id, OLD.title, OLD.content_snapshot);
    INSERT INTO hot_items_fts(rowid, title, content_snapshot)
    VALUES (NEW.id, NEW.title, NEW.content_snapshot);
END;

CREATE TRIGGER IF NOT EXISTS hot_items_ad AFTER DELETE ON hot_items BEGIN
    INSERT INTO hot_items_fts(hot_items_fts, rowid, title, content_snapshot)
    VALUES ('delete', OLD.id, OLD.title, OLD.content_snapshot);
END;

CREATE TRIGGER IF NOT EXISTS tasks_ai AFTER INSERT ON tasks BEGIN
    INSERT INTO tasks_fts(rowid, title, idea_summary, ai_summary)
    VALUES (NEW.id, NEW.title, NEW.idea_summary, NEW.ai_summary);
END;

CREATE TRIGGER IF NOT EXISTS tasks_au AFTER UPDATE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, title, idea_summary, ai_summary)
    VALUES ('delete', OLD.id, OLD.title, OLD.idea_summary, OLD.ai_summary);
    INSERT INTO tasks_fts(rowid, title, idea_summary, ai_summary)
    VALUES (NEW.id, NEW.title, NEW.idea_summary, NEW.ai_summary);
END;

CREATE TRIGGER IF NOT EXISTS tasks_ad AFTER DELETE ON tasks BEGIN
    INSERT INTO tasks_fts(tasks_fts, rowid, title, idea_summary, ai_summary)
    VALUES ('delete', OLD.id, OLD.title, OLD.idea_summary, OLD.ai_summary);
END;

CREATE TRIGGER IF NOT EXISTS outputs_ai AFTER INSERT ON outputs BEGIN
    INSERT INTO outputs_fts(outputs_fts, rowid, content, task_id)
    SELECT 'delete', id, content, task_id FROM outputs
    WHERE task_id = NEW.task_id AND id <> NEW.id
      AND version = (SELECT MAX(version) FROM outputs WHERE task_id = NEW.task_id AND id <> NEW.id);
    INSERT INTO outputs_fts(rowid, content, task_id)
    SELECT NEW.id, NEW.content, NEW.task_id
    WHERE (SELECT MAX(version) FROM outputs WHERE task_id = NEW.task_id) = NEW.version;
END;

CREATE TRIGGER IF NOT EXISTS outputs_au AFTER UPDATE ON outputs BEGIN
    INSERT INTO outputs_fts(outputs_fts, rowid, content, task_id)
    VALUES ('delete', OLD.id, OLD.content, OLD.task_id);
    INSERT INTO outputs_fts(rowid, content, task_id)
    SELECT NEW.id, NEW.content, NEW.task_id
    WHERE (SELECT MAX(version) FROM outputs WHERE task_id = NEW.task_id) = NEW.version;
END;

CREATE TRIGGER IF NOT EXISTS outputs_ad AFTER DELETE ON outputs BEGIN
    INSERT INTO outputs_fts(outputs_fts, rowid, content, task_id)
    VALUES ('delete', OLD.id, OLD.content, OLD.task_id);
END;
"""


SETTINGS = (
    ('score_todo_threshold', '8', 'int'),
    ('collect_interval_hours', '24', 'int'),
    ('daily_budget_tokens', '50000', 'int'),
    ('score_dimensions', json.dumps(['facts', 'verification', 'timeliness', 'value']), 'json'),
    ('generate_count', '10', 'int'),
    ('done_column_limit', '50', 'int'),
    ('discard_retention_days', '7', 'int'),
)


def connect(path: str) -> sqlite3.Connection:
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.executemany(
        'INSERT OR IGNORE INTO settings (key, value, value_type) VALUES (?, ?, ?)',
        SETTINGS,
    )
    conn.execute('INSERT OR REPLACE INTO schema_version (version) VALUES (1)')
    conn.commit()


def backup(db_path: str, dest_path: str) -> None:
    with sqlite3.connect(db_path) as src, sqlite3.connect(dest_path) as dst:
        src.backup(dst)
