"""Task S1.2: db.py v2 schema + FTS5 触发器 + settings 种子 + backup 测试。

FTS 断言遵循 .superpowers/sdd/2026-08-14-idea-hub-v2-implementation/fts5-implementation-guide.md：
- 只用 `WHERE x_fts MATCH ?` 验证索引同步（external-content 表 full-scan SELECT rowid 透传内容表，不反映索引状态）
- 写入/查询顺序：先全部写入 → commit → 再查询（两次写入间穿插 SELECT 会损坏 delete 命令）
- trigram tokenizer 要求查询词/被查文本 ≥3 字符，故用 4 字以上关键词
"""
import json
import sqlite3

import pytest

from idea_hub import db

EXPECTED_TABLES = {
    "sources", "hot_items", "tasks", "task_links", "tags", "task_tags",
    "settings", "notifications", "outputs", "jobs", "schema_version",
}
EXPECTED_FTS_TABLES = {"hot_items_fts", "tasks_fts", "outputs_fts"}
EXPECTED_TRIGGERS = {
    "hot_items_ai", "hot_items_au", "hot_items_ad",
    "tasks_ai", "tasks_au", "tasks_ad",
    "outputs_ai", "outputs_au", "outputs_ad",
}
# (value, value_type)；json 值按解析后列表比较
EXPECTED_SETTINGS = {
    "score_todo_threshold": ("8", "int"),
    "collect_interval_hours": ("24", "int"),
    "daily_budget_tokens": ("50000", "int"),
    "score_dimensions": (["facts", "verification", "timeliness", "value"], "json"),
    "generate_count": ("10", "int"),
    "done_column_limit": ("50", "int"),
    "discard_retention_days": ("7", "int"),
}


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_schema(c)
    yield c
    c.close()


# ---------- connect / init_schema ----------

def test_connect_creates_parent_dir_and_sets_pragmas(tmp_path):
    p = tmp_path / "nested" / "dir" / "test.db"
    c = db.connect(str(p))
    try:
        assert p.exists(), "db.connect 必须先 makedirs 父目录"
        assert c.row_factory is sqlite3.Row
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        c.close()


def test_init_schema_creates_all_tables(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= tables, f"缺表: {EXPECTED_TABLES - tables}"
    fts = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%_fts'")}
    assert fts == EXPECTED_FTS_TABLES, f"FTS 表不符: {fts}"


def test_init_schema_idempotent(tmp_path):
    c = db.connect(str(tmp_path / "x.db"))
    db.init_schema(c)
    db.init_schema(c)  # 二次调用不得抛错
    n = c.execute("SELECT count(*) FROM settings").fetchone()[0]
    c.close()
    assert n == len(EXPECTED_SETTINGS)


def test_sources_type_has_no_check_constraint(conn):
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='sources'"
    ).fetchone()[0]
    assert "CHECK (type IN" not in sql
    conn.execute(
        "INSERT INTO sources (type, name, url) VALUES ('custom-channel', 'x', 'http://x')"
    )
    conn.commit()
    assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 1


# ---------- settings 种子 ----------

def test_settings_seeded(conn):
    rows = {r["key"]: r for r in conn.execute("SELECT key, value, value_type FROM settings")}
    assert set(rows) == set(EXPECTED_SETTINGS), f"settings 键集合不符: {set(rows)}"
    for key, (value, value_type) in EXPECTED_SETTINGS.items():
        assert rows[key]["value_type"] == value_type, key
        if value_type == "json":
            assert json.loads(rows[key]["value"]) == value, key
        else:
            assert rows[key]["value"] == value, key


# ---------- FTS 触发器存在性 ----------

def test_fts_triggers_exist(conn):
    triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert EXPECTED_TRIGGERS <= triggers, f"缺触发器: {EXPECTED_TRIGGERS - triggers}"


# ---------- backup ----------

def test_backup_creates_consistent_copy_with_uncheckpointed_wal(tmp_path):
    db_path = str(tmp_path / "live.db")
    conn = db.connect(db_path)
    db.init_schema(conn)
    conn.execute("INSERT INTO sources (type, name, url) VALUES ('rss', '示例源', 'http://example.com')")
    conn.execute("INSERT INTO tags (name, color) VALUES ('AI', '#ff0000')")
    conn.commit()  # WAL 模式：数据仍在 -wal 中、未 checkpoint
    dest = str(tmp_path / "backup.db")
    db.backup(db_path, dest)
    conn.close()

    b = sqlite3.connect(dest)
    try:
        assert b.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        assert b.execute("SELECT count(*) FROM tags").fetchone()[0] == 1
        assert b.execute("SELECT name, color FROM tags").fetchone() == ("AI", "#ff0000")
        # 备份中 schema 完整（含 outputs 表）
        assert b.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='outputs'"
        ).fetchone()[0] == 1
    finally:
        b.close()


# ---------- FTS 同步（MATCH + 先写后查） ----------

def test_fts_syncs_hot_items_on_insert_update_delete(conn):
    conn.execute("INSERT INTO sources (type, name, url) VALUES ('rss', '源', 'http://example.com')")
    sid = conn.execute("SELECT id FROM sources").fetchone()[0]
    cursor = conn.execute(
        "INSERT INTO hot_items (source_id, title, content_snapshot) VALUES (?, '量子计算突破', '量子计算研究团队发布新成果')",
        (sid,))
    hid = cursor.lastrowid
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM hot_items_fts WHERE hot_items_fts MATCH '量子计算'").fetchone()[0] == 1

    conn.execute("UPDATE hot_items SET title='量子计算产业化进展' WHERE id=?", (hid,))
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM hot_items_fts WHERE hot_items_fts MATCH '产业化'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM hot_items_fts WHERE hot_items_fts MATCH '量子计算'").fetchone()[0] == 1

    conn.execute("DELETE FROM hot_items WHERE id=?", (hid,))
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM hot_items_fts WHERE hot_items_fts MATCH '量子计算'").fetchone()[0] == 0


def test_fts_syncs_tasks_on_insert(conn):
    conn.execute(
        "INSERT INTO tasks (title, idea_summary, ai_summary, status) VALUES "
        "('多智能体协作框架', '多智能体协作研究摘要', '多智能体协作成文总结', 'todo')")
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM tasks_fts WHERE tasks_fts MATCH '多智能体协作'").fetchone()[0] == 1


def test_fts_outputs_indexes_only_latest_version(conn):
    """outputs_fts 每个 task 至多一行：插入 v2 后旧 v1 的 FTS 行被 delete 命令移除（spec 5.3 先删后插）。"""
    conn.execute("INSERT INTO tasks (title, status) VALUES ('测试任务', 'done')")
    tid = conn.execute("SELECT id FROM tasks").fetchone()[0]
    conn.execute(
        "INSERT INTO outputs (task_id, version, filename, content) VALUES (?, 1, 'output.md', '第一版草稿内容全文')",
        (tid,))
    conn.execute(
        "INSERT INTO outputs (task_id, version, filename, content) VALUES (?, 2, 'output.md', '第二版正式成文内容')",
        (tid,))
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM outputs_fts WHERE outputs_fts MATCH '第二版正式成文'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM outputs_fts WHERE outputs_fts MATCH '第一版草稿内容'").fetchone()[0] == 0


def test_fts_outputs_update_refreshes_index(conn):
    """外部文件回写：UPDATE 不递增版本，FTS 先删旧行（OLD 值）再插新行（NEW 值）。"""
    conn.execute("INSERT INTO tasks (title, status) VALUES ('测试任务', 'done')")
    tid = conn.execute("SELECT id FROM tasks").fetchone()[0]
    conn.execute(
        "INSERT INTO outputs (task_id, version, filename, content) VALUES (?, 1, 'output.md', '初版内容全文')",
        (tid,))
    conn.commit()
    oid = conn.execute("SELECT id FROM outputs WHERE task_id=?", (tid,)).fetchone()[0]
    conn.execute("UPDATE outputs SET content='回写修改后的内容' WHERE id=?", (oid,))
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM outputs_fts WHERE outputs_fts MATCH '回写修改后'").fetchone()[0] == 1
    assert conn.execute(
        "SELECT count(*) FROM outputs_fts WHERE outputs_fts MATCH '初版内容'").fetchone()[0] == 0


def test_fts_outputs_delete_removes_index(conn):
    conn.execute("INSERT INTO tasks (title, status) VALUES ('测试任务', 'done')")
    tid = conn.execute("SELECT id FROM tasks").fetchone()[0]
    conn.execute(
        "INSERT INTO outputs (task_id, version, filename, content) VALUES (?, 1, 'output.md', '待删除的产物内容')",
        (tid,))
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM outputs_fts WHERE outputs_fts MATCH '待删除的产物'").fetchone()[0] == 1
    conn.execute("DELETE FROM outputs WHERE task_id=?", (tid,))
    conn.commit()
    assert conn.execute(
        "SELECT count(*) FROM outputs_fts WHERE outputs_fts MATCH '待删除的产物'").fetchone()[0] == 0
