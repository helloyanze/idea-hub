# Idea Hub Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Idea Hub — a local web app plus Hermes cron system that daily collects hot topics, generates scored ideas, and runs a five-queue task pipeline (archived/todo/waiting/in_progress/done) with markdown-persisted drafts and outputs.

**Architecture:** FastAPI serves a single-page kanban frontend backed by one SQLite database (the single source of truth). Python CLI primitives (collect/add-idea/relate/next/complete/fail) perform all data operations; Hermes cron agents provide the AI — idea generation, re-scoring on related hot items, and task execution. Drafts and outputs persist as markdown under `outputs/tasks/<task_id>/`.

**Tech Stack:** Python 3.11, FastAPI + uvicorn, SQLite (WAL), requests + feedparser, pytest, vanilla JS + SortableJS (vendored locally, no CDN).

## Global Constraints

- Python 3.11+; manage deps with uv: `uv venv .venv && uv pip install -r requirements.txt`
- All data in `data/idea.db`, SQLite WAL mode (`PRAGMA journal_mode=WAL`)
- Task status values exactly: `archived`, `todo`, `waiting`, `in_progress`, `done`
- Feasibility threshold: `score >= 6 → todo`, `score < 6 → archived`
- Manual score edits never auto-move queues (spec decision)
- Drafts: `outputs/tasks/<task_id>/idea.md`; execution results: `outputs/tasks/<task_id>/output.md`
- `data/` and `outputs/` are gitignored; `backups/` keeps the last 7 daily DB copies
- Tests run with `pytest` from the project root; each test uses an isolated temp DB (see conftest)
- No auth, no cloud, no notifications (YAGNI per spec)

---

### Task 1: Project skeleton + database layer

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `idea_hub/__init__.py`
- Create: `idea_hub/db.py`
- Create: `idea_hub/models.py`
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing (project root, no prior tasks)
- Produces:
  - `db.connect(path: str) -> sqlite3.Connection` — WAL on, `sqlite3.Row` row factory
  - `db.init_schema(conn) -> None` — creates all tables (targets, sources, hot_items, task_links, tasks, settings, execute_requests)
  - `db.backup_db(conn, backups_dir: str) -> str` — copies DB file, prunes to newest 7
  - `models.STATUSES: tuple[str, ...]`
  - `models.create_task(conn, *, title, idea_summary, target_id, hot_item_id=None, feasibility_score, score_breakdown, idea_path, notes="") -> int`
  - `models.get_task(conn, task_id: int) -> dict | None`
  - `models.list_tasks(conn, status: str | None = None, target_id: int | None = None) -> list[dict]`
  - `models.update_task(conn, task_id: int, **fields) -> None`
  - `models.try_start_task(conn, task_id: int) -> bool` — atomic `waiting → in_progress`, returns False if not waiting
  - `models.move_task(conn, task_id: int, to_status: str) -> None` — any other transition
  - `models.stats(conn, target_id: int | None = None) -> dict[str, int]`

- [ ] **Step 1: Write the failing tests** (`tests/test_models.py`, `tests/conftest.py`)

```python
# tests/conftest.py
import sqlite3, pathlib, pytest
from idea_hub import db

@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_schema(c)
    yield c
    c.close()

@pytest.fixture()
def target_id(conn):
    conn.execute("INSERT INTO targets (name, description, score_dimensions, is_active) VALUES (?, ?, ?, 1)",
                 ("自媒体内容", "test", '{"热度":0.4,"相关性":0.3,"可执行性":0.3}'))
    conn.commit()
    return conn.execute("SELECT id FROM targets").fetchone()[0]
```

```python
# tests/test_models.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'idea_hub'`

- [ ] **Step 3: Write minimal implementation**

```python
# idea_hub/db.py
import sqlite3, pathlib, shutil

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
    dest = pathlib.Path(backups_dir) / f"idea-{pathlib.datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    shutil.copy2(db_path, dest)
    copies = sorted(pathlib.Path(backups_dir).glob("idea-*.db"), reverse=True)
    for old in copies[7:]:
        old.unlink()
    return str(dest)
```

```python
# idea_hub/models.py
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
               "ai_summary", "output_path", "notes", "status"}
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
```

```python
# idea_hub/__init__.py
"""Idea Hub — daily hot-topic idea pipeline."""
__version__ = "0.1.0"
```

```python
# .gitignore
.venv/
__pycache__/
data/
outputs/
backups/
*.pyc
```

```text
# requirements.txt
fastapi>=0.115
uvicorn>=0.30
requests>=2.32
feedparser>=6.0
pytest>=8.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add .gitignore requirements.txt idea_hub/ tests/
git commit -m "feat: database layer with schema, CRUD, atomic status transitions"
```

---

### Task 2: Targets, sources, settings CRUD

**Files:**
- Modify: `idea_hub/models.py` (append functions)
- Create: `tests/test_models.py` (append tests)

**Interfaces:**
- Consumes: `db.connect`, `db.init_schema` (Task 1)
- Produces:
  - `models.create_target(conn, *, name, description, score_dimensions: str) -> int`
  - `models.activate_target(conn, target_id: int) -> None` — deactivates all others first
  - `models.get_active_target(conn) -> dict | None`
  - `models.list_targets(conn) -> list[dict]`
  - `models.create_source(conn, *, type: str, name, url, enabled: bool = True) -> int`
  - `models.list_sources(conn, enabled_only: bool = False) -> list[dict]`
  - `models.set_source_enabled(conn, source_id: int, enabled: bool) -> None`
  - `models.get_setting(conn, key: str, default=None)`
  - `models.set_setting(conn, key: str, value: str) -> None`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_models.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `AttributeError: module 'idea_hub.models' has no attribute 'create_target'`

- [ ] **Step 3: Write minimal implementation** (append to `idea_hub/models.py`)

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add idea_hub/models.py tests/test_models.py
git commit -m "feat: targets, sources, settings CRUD"
```

---

### Task 3: Hot topic collection (hotlist API + RSS)

**Files:**
- Create: `idea_hub/collectors.py`
- Create: `idea_hub/cli.py`
- Create: `tests/test_collectors.py`

**Interfaces:**
- Consumes: `models.list_sources(conn, enabled_only=True)`, `db.connect` (Tasks 1-2)
- Produces:
  - `collectors.fetch_hotlist(url: str, items_path: str = "data") -> list[dict]` — GET JSON; `items_path` is a dot path to the array (e.g. `data` or `result.list`); each item dict needs `title` and `url` keys; also captures `hot` / `rank` / `desc` keys when present
  - `collectors.fetch_rss(url: str) -> list[dict]` — feedparser; items with `title`, `url` (link), `content_snapshot` (summary)
  - `collectors.collect_all(conn) -> dict` — runs every enabled source; returns `{"collected": int, "errors": [str]}`; skips a source on error, records error
  - `cli` subcommands (argparse, run via `python -m idea_hub.cli`): `collect`
- Test doubles: a local JSON file via `file://` is NOT supported by requests; instead `fetch_hotlist` takes an optional `session` param, and tests pass a fake session object.

- [ ] **Step 1: Write the failing tests** (`tests/test_collectors.py`)

```python
import json
from idea_hub import db, collectors, models

class FakeResp:
    def __init__(self, payload): self._payload = payload
    def json(self): return self._payload
    def raise_for_status(self): pass

class FakeSession:
    def __init__(self, payload): self.payload = payload
    def get(self, url, timeout=None): return FakeResp(self.payload)

def test_fetch_hotlist_nested_path():
    payload = {"result": {"list": [{"title": "A", "url": "http://a", "hot": 99}]}}
    items = collectors.fetch_hotlist("http://x", items_path="result.list", session=FakeSession(payload))
    assert items[0]["title"] == "A"
    assert items[0]["content_snapshot"] == "热度:99"

def test_collect_all_dedupes(conn, tmp_path):
    sid = models.create_source(conn, type="hotlist", name="测试榜",
                               url=f"file://{tmp_path}/nope.json")
    payload = {"data": [{"title": "T1", "url": "http://u1"},
                        {"title": "T2", "url": "http://u2"}]}
    # monkeypatch: use fake session for any url
    orig = collectors.fetch_hotlist
    collectors.fetch_hotlist = lambda url, items_path="data", session=None: orig(url, items_path=items_path, session=FakeSession(payload))
    try:
        res = collectors.collect_all(conn)
        assert res["collected"] == 2
        res2 = collectors.collect_all(conn)
        assert res2["collected"] == 0  # dedupe
    finally:
        collectors.fetch_hotlist = orig

def test_rss_items_shape(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text('<?xml version="1.0"?><rss version="2.0"><channel>'
                    '<item><title>R1</title><link>http://r1</link>'
                    '<description>desc1</description></item>'
                    '</channel></rss>', encoding="utf-8")
    items = collectors.fetch_rss(feed.as_uri())
    assert items[0]["title"] == "R1"
    assert items[0]["url"] == "http://r1"
    assert "desc1" in items[0]["content_snapshot"]
```

Note: RSS is tested end-to-end in `test_collect_all_rss` using a real temp RSS file — see Step 3. The `feedparser.parse` call accepts a local file path directly, so use `url=f"file://{tmp_path}/feed.xml"` with a real XML file written to disk.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_collectors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'idea_hub.collectors'`

- [ ] **Step 3: Write minimal implementation**

```python
# idea_hub/collectors.py
import json
import requests
import feedparser
from idea_hub import models

def _dig(obj, path):
    for part in path.split("."):
        if isinstance(obj, dict): obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit(): obj = obj[int(part)]
        else: return None
    return obj

def fetch_hotlist(url, items_path="data", session=None):
    s = session or requests
    resp = s.get(url, timeout=15)
    resp.raise_for_status()
    data = _dig(resp.json(), items_path) or []
    out = []
    for it in data:
        item = {"title": str(it.get("title", "")).strip(),
                "url": str(it.get("url", "")).strip()}
        parts = []
        if it.get("hot") is not None: parts.append(f"热度:{it['hot']}")
        if it.get("rank") is not None: parts.append(f"排名:{it['rank']}")
        if it.get("desc"): parts.append(str(it["desc"]))
        item["content_snapshot"] = " ".join(parts)
        if item["title"] and item["url"]:
            out.append(item)
    return out

def fetch_rss(url):
    feed = feedparser.parse(url)
    out = []
    for e in feed.entries:
        out.append({"title": getattr(e, "title", "").strip(),
                    "url": getattr(e, "link", "").strip(),
                    "content_snapshot": (getattr(e, "summary", "") or "")[:500]})
    return [i for i in out if i["title"] and i["url"]]

def _upsert_hot_item(conn, source_id, item):
    cur = conn.execute("INSERT OR IGNORE INTO hot_items (source_id, title, url, content_snapshot) "
                       "VALUES (?,?,?,?)", (source_id, item["title"], item["url"], item["content_snapshot"]))
    return cur.rowcount

def collect_all(conn):
    collected, errors = 0, []
    for src in models.list_sources(conn, enabled_only=True):
        try:
            items = fetch_rss(src["url"]) if src["type"] == "rss" else fetch_hotlist(src["url"])
            for it in items:
                collected += _upsert_hot_item(conn, src["id"], it)
            conn.commit()
        except Exception as exc:
            errors.append(f"{src['name']}: {exc}")
    return {"collected": collected, "errors": errors}
```

```python
# idea_hub/cli.py
import argparse, sys
from idea_hub import db, collectors, models

def _conn(args):
    c = db.connect(args.db)
    db.init_schema(c)
    return c

def cmd_collect(args):
    conn = _conn(args)
    res = collectors.collect_all(conn)
    print(f"collected={res['collected']}")
    for e in res["errors"]:
        print(f"ERROR: {e}", file=sys.stderr)

def main():
    p = argparse.ArgumentParser(prog="idea_hub")
    p.add_argument("--db", default="data/idea.db")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect").set_defaults(func=cmd_collect)
    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_collectors.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add idea_hub/collectors.py idea_hub/cli.py tests/test_collectors.py
git commit -m "feat: hotlist+RSS collection with dedupe"
```

---

### Task 4: Idea generation + related-hotitem re-scoring (CLI primitives)

**Files:**
- Modify: `idea_hub/cli.py` (append subcommands)
- Create: `tests/test_cli_flow.py`

**Interfaces:**
- Consumes: `models.create_task`, `models.move_task`, `models.update_task`, `models.list_tasks`, `models.get_active_target`, `models.get_setting` (Tasks 1-2), `models.list_tasks` filter by status
- Produces (CLI subcommands, all run via `python -m idea_hub.cli`):
  - `candidates` — prints JSON lines of today's hot items with no linked task yet: `{"id", "title", "url", "content_snapshot"}`
  - `add-idea --hot-item-id N --title T --summary S --score N --dims JSON --detail-path FILE` — writes task; copies `FILE` content to `outputs/tasks/<new_id>/idea.md`; creates the task_links row; prints new task id. Threshold rule from Task 1 applies (score >= 6 → todo, else archived)
  - `relate --task-id N --hot-item-id M --score N --dims JSON --detail-path FILE` — appends task_links row, updates idea.md content (append section), updates score + score_breakdown via `update_task`; **if new score >= 6 and status == 'archived', moves to 'todo'**; prints new status
  - Helper `_write_task_draft(task_id: int, content: str, base: pathlib.Path) -> str` — writes `outputs/tasks/<id>/idea.md`, returns relative path

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli_flow.py`)

```python
import json, pathlib, subprocess, sys
from idea_hub import db, models

def _run_cli(args, tmp_path):
    return subprocess.run([sys.executable, "-m", "idea_hub.cli", "--db", str(tmp_path / "t.db"), *args],
                          capture_output=True, text=True, cwd=pathlib.Path(__file__).parent.parent)

def _seed(conn):
    models.create_target(conn, name="自媒体内容", description="d", score_dimensions="{}")
    models.activate_target(conn, 1)
    models.create_source(conn, type="hotlist", name="榜", url="http://x")
    conn.execute("INSERT INTO hot_items (source_id, title, url, content_snapshot) VALUES (1, '热点X', 'http://x', 'snap')")
    conn.commit()

def test_add_idea_threshold_and_draft(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn); conn.close()
    draft = tmp_path / "draft.md"; draft.write_text("# 构思全文\n内容", encoding="utf-8")
    r = _run_cli(["add-idea", "--hot-item-id", "1", "--title", "写一篇X文章",
                  "--summary", "摘要", "--score", "7", "--dims", '{"热度":8}',
                  "--detail-path", str(draft)], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    task = models.get_task(conn, 1)
    assert task["status"] == "todo"
    assert pathlib.Path("outputs/tasks/1/idea.md").exists()
    conn.close()

def test_relate_rescores_archived_to_todo(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="旧想法", idea_summary="s", target_id=1,
                       hot_item_id=1, feasibility_score=5, score_breakdown="{}",
                       idea_path="outputs/tasks/1/idea.md")  # archived
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点Y', 'http://y')")
    conn.commit(); conn.close()
    draft = tmp_path / "draft2.md"; draft.write_text("补充信息", encoding="utf-8")
    r = _run_cli(["relate", "--task-id", "1", "--hot-item-id", "2",
                  "--score", "7", "--dims", '{"热度":9}', "--detail-path", str(draft)], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    task = models.get_task(conn, 1)
    assert task["status"] == "todo"
    assert task["feasibility_score"] == 7
    links = conn.execute("SELECT hot_item_id FROM task_links WHERE task_id=1").fetchall()
    assert {l["hot_item_id"] for l in links} == {1, 2}
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_flow.py -v`
Expected: FAIL — `error: argument cmd: invalid choice: 'add-idea'`

- [ ] **Step 3: Write minimal implementation** (append to `idea_hub/cli.py`)

```python
import json, pathlib

def _link_exists(conn, task_id, hot_item_id):
    return conn.execute("SELECT 1 FROM task_links WHERE task_id=? AND hot_item_id=?",
                        (task_id, hot_item_id)).fetchone() is not None

def _write_draft(base, task_id, content):
    d = pathlib.Path(base) / "outputs" / "tasks" / str(task_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "idea.md"
    p.write_text(content, encoding="utf-8")
    return str(pathlib.Path("outputs") / "tasks" / str(task_id) / "idea.md").replace("\\", "/")

def cmd_candidates(args):
    conn = _conn(args)
    linked = {r["hot_item_id"] for r in conn.execute("SELECT hot_item_id FROM task_links").fetchall()}
    rows = conn.execute("SELECT id, title, url, content_snapshot FROM hot_items "
                        "WHERE date(collected_at)=date('now') ORDER BY id").fetchall()
    for r in rows:
        if r["id"] not in linked:
            print(json.dumps(dict(r), ensure_ascii=False))

def cmd_add_idea(args):
    conn = _conn(args)
    content = pathlib.Path(args.detail_path).read_text(encoding="utf-8")
    tid = models.create_task(conn, title=args.title, idea_summary=args.summary,
                             target_id=models.get_active_target(conn)["id"],
                             hot_item_id=args.hot_item_id, feasibility_score=args.score,
                             score_breakdown=args.dims, idea_path="")
    models.update_task(conn, tid, idea_path=_write_draft(args.base, tid, content))
    conn.execute("INSERT OR IGNORE INTO task_links (task_id, hot_item_id) VALUES (?,?)",
                 (tid, args.hot_item_id))
    conn.commit()
    print(tid)

def cmd_relate(args):
    conn = _conn(args)
    if not _link_exists(conn, args.task_id, args.hot_item_id):
        conn.execute("INSERT INTO task_links (task_id, hot_item_id) VALUES (?,?)",
                     (args.task_id, args.hot_item_id))
        conn.commit()
    task = models.get_task(conn, args.task_id)
    content = pathlib.Path(task["idea_path"]).read_text(encoding="utf-8") if task["idea_path"] else ""
    addition = pathlib.Path(args.detail_path).read_text(encoding="utf-8")
    models.update_task(conn, args.task_id, feasibility_score=args.score,
                       score_breakdown=args.dims,
                       idea_path=_write_draft(args.base, args.task_id, content + "\n\n## 新增关联信息\n" + addition))
    task = models.get_task(conn, args.task_id)
    new_status = task["status"]
    if task["feasibility_score"] >= models.SCORE_THRESHOLD and task["status"] == "archived":
        models.move_task(conn, args.task_id, "todo"); new_status = "todo"
    print(new_status)

def _add_parser(sub, name, help_):
    return sub.add_parser(name, help=help_)

def main():
    p = argparse.ArgumentParser(prog="idea_hub")
    p.add_argument("--db", default="data/idea.db")
    p.add_argument("--base", default=str(pathlib.Path.cwd()))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect").set_defaults(func=cmd_collect)
    sub.add_parser("candidates").set_defaults(func=cmd_candidates)
    pa = sub.add_parser("add-idea")
    pa.add_argument("--hot-item-id", type=int, required=True)
    pa.add_argument("--title", required=True)
    pa.add_argument("--summary", required=True)
    pa.add_argument("--score", type=int, required=True)
    pa.add_argument("--dims", required=True)
    pa.add_argument("--detail-path", required=True)
    pa.set_defaults(func=cmd_add_idea)
    pr = sub.add_parser("relate")
    pr.add_argument("--task-id", type=int, required=True)
    pr.add_argument("--hot-item-id", type=int, required=True)
    pr.add_argument("--score", type=int, required=True)
    pr.add_argument("--dims", required=True)
    pr.add_argument("--detail-path", required=True)
    pr.set_defaults(func=cmd_relate)
    args = p.parse_args()
    args.func(args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_flow.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add idea_hub/cli.py tests/test_cli_flow.py
git commit -m "feat: add-idea and relate CLI primitives with draft persistence"
```

---

### Task 5: Execution primitives (next / complete / fail) + execute_requests

**Files:**
- Modify: `idea_hub/cli.py`
- Append: `tests/test_cli_flow.py`

**Interfaces:**
- Consumes: `models.try_start_task`, `models.move_task`, `models.update_task` (Task 1)
- Produces (CLI subcommands):
  - `next` — atomically takes the oldest `waiting` task → `in_progress`; prints its full JSON (id, title, idea_path, notes); exits 1 with message if queue empty
  - `complete --task-id N --summary S --output-path P` — writes `outputs/tasks/<id>/output.md` (content read from `P`), sets `ai_summary`, `output_path`, moves to `done`
  - `fail --task-id N --reason R` — appends `[失败] <reason>` to notes, moves back to `waiting`
  - `pending-executions` — lists pending `execute_requests` rows (for the Hermes execution cron)
  - `resolve-execution --task-id N` — marks the execute_request done

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli_flow.py`)

```python
def test_next_complete_fail_cycle(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="任务", idea_summary="s", target_id=1, hot_item_id=1,
                       feasibility_score=7, score_breakdown="{}", idea_path="")
    models.move_task(conn, 1, "waiting")
    conn.close()
    r = _run_cli(["next"], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    assert models.get_task(conn, 1)["status"] == "in_progress"
    conn.close()
    out = tmp_path / "out.md"; out.write_text("# 产出\n正文", encoding="utf-8")
    r = _run_cli(["complete", "--task-id", "1", "--summary", "完成摘要",
                  "--output-path", str(out)], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    t = models.get_task(conn, 1)
    assert t["status"] == "done" and t["ai_summary"] == "完成摘要"
    assert pathlib.Path("outputs/tasks/1/output.md").exists()
    conn.close()

def test_fail_returns_to_waiting(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="任务", idea_summary="s", target_id=1, hot_item_id=1,
                       feasibility_score=7, score_breakdown="{}", idea_path="")
    models.move_task(conn, 1, "waiting"); conn.close()
    _run_cli(["next"], tmp_path)
    r = _run_cli(["fail", "--task-id", "1", "--reason", "超时"], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    t = models.get_task(conn, 1)
    assert t["status"] == "waiting" and "超时" in t["notes"]
    conn.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_flow.py -v`
Expected: FAIL — `invalid choice: 'next'`

- [ ] **Step 3: Write minimal implementation** (append to `idea_hub/cli.py`)

```python
def cmd_next(args):
    conn = _conn(args)
    row = conn.execute("SELECT id FROM tasks WHERE status='waiting' ORDER BY updated_at LIMIT 1").fetchone()
    if not row:
        print("queue empty"); sys.exit(1)
    if not models.try_start_task(conn, row["id"]):
        print("queue empty"); sys.exit(1)
    task = models.get_task(conn, row["id"])
    print(json.dumps(task, ensure_ascii=False))

def cmd_complete(args):
    conn = _conn(args)
    content = pathlib.Path(args.output_path).read_text(encoding="utf-8")
    d = pathlib.Path(args.base) / "outputs" / "tasks" / str(args.task_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "output.md"
    p.write_text(content, encoding="utf-8")
    rel = str(pathlib.Path("outputs") / "tasks" / str(args.task_id) / "output.md").replace("\\", "/")
    models.update_task(conn, args.task_id, ai_summary=args.summary, output_path=rel)
    models.move_task(conn, args.task_id, "done")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (args.task_id,))
    conn.commit()
    print("done")

def cmd_fail(args):
    conn = _conn(args)
    task = models.get_task(conn, args.task_id)
    models.update_task(conn, args.task_id, notes=f"{task['notes']}\n[失败] {args.reason}".strip())
    models.move_task(conn, args.task_id, "waiting")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (args.task_id,))
    conn.commit()
    print("waiting")

def cmd_pending_executions(args):
    conn = _conn(args)
    for r in conn.execute("SELECT task_id FROM execute_requests WHERE status='pending' ORDER BY id").fetchall():
        print(r["task_id"])

def cmd_resolve_execution(args):
    conn = _conn(args)
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=?", (args.task_id,))
    conn.commit()
```

Register subcommands in `main()`:

```python
    sub.add_parser("next").set_defaults(func=cmd_next)
    pc = sub.add_parser("complete")
    pc.add_argument("--task-id", type=int, required=True)
    pc.add_argument("--summary", required=True)
    pc.add_argument("--output-path", required=True)
    pc.set_defaults(func=cmd_complete)
    pf = sub.add_parser("fail")
    pf.add_argument("--task-id", type=int, required=True)
    pf.add_argument("--reason", required=True)
    pf.set_defaults(func=cmd_fail)
    sub.add_parser("pending-executions").set_defaults(func=cmd_pending_executions)
    sub.add_parser("resolve-execution", add_help=False).set_defaults(func=cmd_resolve_execution)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_flow.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add idea_hub/cli.py tests/test_cli_flow.py
git commit -m "feat: execution primitives next/complete/fail with execute_requests"
```

---

### Task 6: FastAPI backend

**Files:**
- Create: `idea_hub/server.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Consumes: all `models.*` functions, `db.connect`, `db.init_schema` (Tasks 1-5)
- Produces:
  - `server.create_app(db_path: str) -> FastAPI` — testable app factory
  - Routes:
    - `GET /api/stats`
    - `GET /api/tasks?status=&target_id=`
    - `GET /api/tasks/{task_id}` — includes `idea_full` (content of idea_path file, if any)
    - `POST /api/tasks/{task_id}/move` body `{"to_status": "..."}`
    - `PATCH /api/tasks/{task_id}` body: any of `title, idea_summary, feasibility_score, score_breakdown, notes`
    - `POST /api/tasks/{task_id}/execute` — inserts an `execute_requests` row (pending); the Hermes execution cron polls this
    - `GET /api/targets`, `POST /api/targets`, `POST /api/targets/{id}/activate`
    - `GET /api/sources`, `POST /api/sources`, `DELETE /api/sources/{id}`, `POST /api/sources/{id}/toggle`
    - `GET /api/settings`, `PUT /api/settings` body `{"key": ..., "value": ...}`
    - `GET /` — serves `web/index.html` (static mount from Task 7; stub route here returning 404 until then)
- Static file serving for `web/` is added in Task 7; `create_app` mounts it only if the directory exists.

- [ ] **Step 1: Write the failing tests** (`tests/test_api.py`)

```python
import json, pytest
from fastapi.testclient import TestClient
from idea_hub import db, models, server

@pytest.fixture  # noqa: F401 (used via conftest pattern; define locally)
def client(tmp_path):
    db_path = str(tmp_path / "api.db")
    conn = db.connect(db_path); db.init_schema(conn)
    models.create_target(conn, name="自媒体", description="d", score_dimensions="{}")
    models.activate_target(conn, 1)
    models.create_source(conn, type="hotlist", name="榜", url="http://x")
    conn.close()
    app = server.create_app(db_path)
    return TestClient(app)

def test_stats_and_task_lifecycle(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = {"title": "想法", "idea_summary": "s", "target_id": 1, "hot_item_id": None,
            "feasibility_score": 8, "score_breakdown": "{}", "idea_path": ""}
    r = client.post("/api/tasks", json=body)
    assert r.status_code == 200
    tid = r.json()["id"]
    r = client.post(f"/api/tasks/{tid}/move", json={"to_status": "waiting"})
    assert r.json()["status"] == "waiting"
    r = client.patch(f"/api/tasks/{tid}", json={"notes": "用户备注"})
    assert r.json()["notes"] == "用户备注"

def test_execute_request_created(client):
    body = {"title": "t", "idea_summary": "s", "target_id": 1, "hot_item_id": None,
            "feasibility_score": 7, "score_breakdown": "{}", "idea_path": ""}
    tid = client.post("/api/tasks", json=body).json()["id"]
    r = client.post(f"/api/tasks/{tid}/execute")
    assert r.status_code == 200
    conn = db.connect(client.app.state.db_path)
    row = conn.execute("SELECT status FROM execute_requests WHERE task_id=?", (tid,)).fetchone()
    assert row["status"] == "pending"
    conn.close()

def test_target_activate(client):
    r = client.post("/api/targets", json={"name": "开发类", "description": "d",
                                          "score_dimensions": "{}"})
    assert r.status_code == 200
    tid = r.json()["id"]
    client.post(f"/api/targets/{tid}/activate")
    assert client.get("/api/targets").json()["items"][0]["is_active"] == 1
```

Note: `POST /api/tasks` is used above — the plan requires it even though the spec mentions task creation only via AI. Add route: `POST /api/tasks` body = task fields, returns created task (useful for manual seeding and tests).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'idea_hub.server'`

- [ ] **Step 3: Write minimal implementation** (`idea_hub/server.py`)

```python
import json, pathlib
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from idea_hub import db, models

class TaskIn(BaseModel):
    title: str
    idea_summary: str = ""
    target_id: int
    hot_item_id: int | None = None
    feasibility_score: int
    score_breakdown: str = "{}"
    idea_path: str = ""
    notes: str = ""

class MoveIn(BaseModel):
    to_status: str

class PatchIn(BaseModel):
    title: str | None = None
    idea_summary: str | None = None
    feasibility_score: int | None = None
    score_breakdown: str | None = None
    notes: str | None = None

class TargetIn(BaseModel):
    name: str
    description: str = ""
    score_dimensions: str = "{}"

class SourceIn(BaseModel):
    type: str
    name: str
    url: str

class SettingIn(BaseModel):
    key: str
    value: str

def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="Idea Hub")
    app.state.db_path = db_path

    def conn():
        c = db.connect(db_path); db.init_schema(c); return c

    @app.get("/api/stats")
    def stats(target_id: int | None = None):
        with conn() as c:
            return models.stats(c, target_id)

    @app.get("/api/tasks")
    def list_tasks(status: str | None = None, target_id: int | None = None):
        with conn() as c:
            return {"items": models.list_tasks(c, status, target_id)}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int):
        with conn() as c:
            t = models.get_task(c, task_id)
            if not t: raise HTTPException(404, "task not found")
            if t["idea_path"] and pathlib.Path(t["idea_path"]).exists():
                t["idea_full"] = pathlib.Path(t["idea_path"]).read_text(encoding="utf-8")
            else:
                t["idea_full"] = ""
            return t

    @app.post("/api/tasks")
    def create_task(body: TaskIn):
        with conn() as c:
            tid = models.create_task(c, title=body.title, idea_summary=body.idea_summary,
                                     target_id=body.target_id, hot_item_id=body.hot_item_id,
                                     feasibility_score=body.feasibility_score,
                                     score_breakdown=body.score_breakdown,
                                     idea_path=body.idea_path, notes=body.notes)
            return models.get_task(c, tid)

    @app.post("/api/tasks/{task_id}/move")
    def move_task(task_id: int, body: MoveIn):
        with conn() as c:
            if not models.get_task(c, task_id): raise HTTPException(404, "task not found")
            models.move_task(c, task_id, body.to_status)
            return models.get_task(c, task_id)

    @app.patch("/api/tasks/{task_id}")
    def patch_task(task_id: int, body: PatchIn):
        with conn() as c:
            if not models.get_task(c, task_id): raise HTTPException(404, "task not found")
            models.update_task(c, task_id, **body.model_dump(exclude_none=True))
            return models.get_task(c, task_id)

    @app.post("/api/tasks/{task_id}/execute")
    def request_execution(task_id: int):
        with conn() as c:
            if not models.get_task(c, task_id): raise HTTPException(404, "task not found")
            c.execute("INSERT INTO execute_requests (task_id) VALUES (?)", (task_id,))
            c.commit()
            return {"ok": True}

    @app.get("/api/targets")
    def list_targets():
        with conn() as c:
            return {"items": models.list_targets(c)}

    @app.post("/api/targets")
    def create_target(body: TargetIn):
        with conn() as c:
            tid = models.create_target(c, name=body.name, description=body.description,
                                       score_dimensions=body.score_dimensions)
            return {"id": tid}

    @app.post("/api/targets/{target_id}/activate")
    def activate_target(target_id: int):
        with conn() as c:
            models.activate_target(c, target_id)
            return {"ok": True}

    @app.get("/api/sources")
    def list_sources():
        with conn() as c:
            return {"items": models.list_sources(c)}

    @app.post("/api/sources")
    def create_source(body: SourceIn):
        with conn() as c:
            sid = models.create_source(c, type=body.type, name=body.name, url=body.url)
            return {"id": sid}

    @app.post("/api/sources/{source_id}/toggle")
    def toggle_source(source_id: int):
        with conn() as c:
            src = next((s for s in models.list_sources(c) if s["id"] == source_id), None)
            if not src: raise HTTPException(404, "source not found")
            models.set_source_enabled(c, source_id, not src["enabled"])
            return {"ok": True}

    @app.delete("/api/sources/{source_id}")
    def delete_source(source_id: int):
        with conn() as c:
            c.execute("DELETE FROM sources WHERE id=?", (source_id,)); c.commit()
            return {"ok": True}

    @app.get("/api/settings")
    def get_settings():
        with conn() as c:
            return {"items": [{"key": k, "value": v} for k, v in
                              c.execute("SELECT key, value FROM settings").fetchall()]}

    @app.put("/api/settings")
    def put_setting(body: SettingIn):
        with conn() as c:
            models.set_setting(c, body.key, body.value)
            return {"ok": True}

    web_dir = pathlib.Path(__file__).parent.parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=web_dir), name="static")
        @app.get("/")
        def index():
            return FileResponse(web_dir / "index.html")

    return app

def main():
    import uvicorn
    uvicorn.run(create_app("data/idea.db"), host="127.0.0.1", port=8000)

if __name__ == "__main__":
    main()
```

Add `from fastapi.staticfiles import StaticFiles` to the imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add idea_hub/server.py tests/test_api.py
git commit -m "feat: FastAPI backend with task/source/target/settings routes"
```

---

### Task 7: Kanban frontend

**Files:**
- Create: `web/index.html`
- Create: `web/style.css`
- Create: `web/app.js`
- Create: `web/vendor/sortable.min.js` (vendored SortableJS 1.15.x — download once from https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js and commit it; offline requirement)

**Interfaces:**
- Consumes: all `/api/*` routes from Task 6
- Produces: the single-page app; `GET /` serves it via the static mount added in Task 6

**Page structure (from spec):**

- Top bar: app title, target switcher (dropdown of `/api/targets`, activate via POST), "来源管理" button (opens modal), "立即收集" button (shows collected count via CLI output note — calls `POST /api/collect` stub? No: keep YAGNI — button shows instructions to run the collect cron; the cron is configured in Task 8), stats line.
- Five columns: 留档 (archived), 待办 (todo), 等待 (waiting), 进行中 (in_progress), 已完成 (done). Each column is a SortableJS group so cards drag across columns.
- Card: title + score badge (red <6, yellow 6-7, green >=8) + first 60 chars of idea_summary.
- Detail panel (right side drawer): idea_full (from GET /api/tasks/{id}), score breakdown JSON pretty-printed, linked hot items, output link (output_path), notes; inline edit: title, idea_summary, feasibility_score (slider 1-10), notes; save via PATCH.
- Dragging = POST /api/tasks/{id}/move with the target column's status.
- 执行 button on waiting/in_progress columns: POST /api/tasks/{id}/execute, then shows "已加入执行队列".
- Target switcher: on change, reload tasks with `?target_id=` filter and stats.
- Sources modal: list sources (GET), add (POST), toggle (POST toggle), delete (DELETE).

- [ ] **Step 1: Write the failing "test" (manual walkthrough checklist — no build step, per spec)**

There is no automated test for vanilla JS with no build step. Instead, after implementation, verify manually per this checklist:

```
1. Open http://127.0.0.1:8000/ — five columns render, empty states show
2. Seed 2 tasks via API (POST /api/tasks) → cards appear in correct columns by score
3. Drag card from 待办 to 等待 → refresh → still in 等待 (persisted)
4. Click card → drawer shows idea_full text
5. Change score slider → save → badge color updates
6. Click 执行 on a waiting card → execute_requests row appears in DB
7. Switch target → tasks filter; stats update
8. Sources modal: add a hotlist source, toggle it off/on, delete it
```

- [ ] **Step 2: Write the failing DOM (index.html skeleton with five columns)**

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Idea Hub</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header id="topbar">
  <h1>Idea Hub</h1>
  <select id="target-switch"></select>
  <button id="btn-sources">来源管理</button>
  <button id="btn-collect">立即收集</button>
  <div id="stats"></div>
</header>
<main id="board">
  <section class="col" data-status="archived"><h2>留档</h2><div class="cards"></div></section>
  <section class="col" data-status="todo"><h2>待办</h2><div class="cards"></div></section>
  <section class="col" data-status="waiting"><h2>等待</h2><div class="cards"></div></section>
  <section class="col" data-status="in_progress"><h2>进行中</h2><div class="cards"></div></section>
  <section class="col" data-status="done"><h2>已完成</h2><div class="cards"></div></section>
</main>
<aside id="drawer" hidden></aside>
<div id="source-modal" hidden></div>
<script src="/static/vendor/sortable.min.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Implement `web/style.css`** — kanban layout: `#board` flex row, `.col` flex:1 with min-width, `.cards` min-height for drop targets, `.badge.red/.yellow/.green`, `#drawer` fixed right panel 480px with slide-in, `#source-modal` overlay. Colors: red `#dc2626`, yellow `#d97706`, green `#16a34a`. Keep it one file, ~150 lines.

- [ ] **Step 4: Implement `web/app.js`** — key logic (full file):

```javascript
const $ = (s) => document.querySelector(s);
const api = {
  get: (p) => fetch(p).then(r => r.json()),
  post: (p, b) => fetch(p, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b||{})}).then(r=>r.json()),
  patch: (p, b) => fetch(p, {method:'PATCH', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b)}).then(r=>r.json()),
};
let currentTarget = null;

const COLUMN_NAMES = {archived:'留档', todo:'待办', waiting:'等待', in_progress:'进行中', done:'已完成'};

function badge(score) {
  const cls = score < 6 ? 'red' : score <= 7 ? 'yellow' : 'green';
  return `<span class="badge ${cls}">${score}</span>`;
}

function cardHTML(t) {
  return `<div class="card" data-id="${t.id}">
    <div class="card-title">${t.title}</div>
    <div class="card-meta">${badge(t.feasibility_score)} ${(t.idea_summary||'').slice(0,60)}</div>
  </div>`;
}

async function loadBoard() {
  const q = currentTarget ? `?target_id=${currentTarget}` : '';
  const data = await api.get(`/api/tasks${q}`);
  const st = await api.get(`/api/stats${q}`);
  for (const [status, col] of Object.entries(COLUMN_NAMES)) {
    const box = document.querySelector(`.col[data-status="${status}"] .cards`);
    box.innerHTML = data.items.filter(t => t.status === status).map(cardHTML).join('');
  }
  $('#stats').textContent = Object.entries(st).map(([k,v]) => `${COLUMN_NAMES[k]||k}:${v}`).join(' | ');
}

function initSortable() {
  document.querySelectorAll('.col .cards').forEach(box => {
    new Sortable(box, {
      group: 'board',
      onEnd: async (evt) => {
        const taskId = Number(evt.item.dataset.id);
        const toStatus = evt.to.closest('.col').dataset.status;
        await api.post(`/api/tasks/${taskId}/move`, {to_status: toStatus});
        loadBoard();
      }
    });
  });
}

async function openDrawer(id) {
  const t = await api.get(`/api/tasks/${id}`);
  $('#drawer').innerHTML = `
    <button onclick="closeDrawer()">关闭</button>
    <h2>${t.title}</h2>
    <label>分数 <input type="range" min="1" max="10" id="f-score" value="${t.feasibility_score}"></label>
    <h3>构思全文</h3><div id="idea-full"></div>
    <h3>评分明细</h3><pre>${t.score_breakdown}</pre>
    <h3>产出</h3><div>${t.output_path ? `<a href="/static/${t.output_path}">打开产出</a>` : '无'}</div>
    <label>备注 <textarea id="f-notes">${t.notes||''}</textarea></label>
    <button onclick="saveTask(${t.id})">保存修改</button>
    <button onclick="runTask(${t.id})">执行</button>`;
  $('#idea-full').textContent = t.idea_full || '(无构思文件)';
  $('#drawer').hidden = false;
}
window.closeDrawer = () => $('#drawer').hidden = true;

async function saveTask(id) {
  await api.patch(`/api/tasks/${id}`, {
    feasibility_score: Number($('#f-score').value),
    notes: $('#f-notes').value
  });
  closeDrawer(); loadBoard();
}

async function runTask(id) {
  await api.post(`/api/tasks/${id}/execute`);
  alert('已加入执行队列');
}

async function loadTargets() {
  const data = await api.get('/api/targets');
  const sel = $('#target-switch');
  sel.innerHTML = data.items.map(t => `<option value="${t.id}" ${t.is_active?'selected':''}>${t.name}</option>`).join('');
  currentTarget = data.items.find(t => t.is_active)?.id ?? null;
  sel.onchange = async () => {
    await api.post(`/api/targets/${sel.value}/activate`);
    currentTarget = Number(sel.value);
    loadBoard();
  };
}

// sources modal: list/add/toggle/delete per Task 6 routes
async function loadSources() {
  const d = await api.get('/api/sources');
  $('#source-modal').innerHTML = `<h3>来源管理</h3>` +
    d.items.map(s => `<div>${s.name} (${s.type}) ${s.enabled?'启用':'停用'}
      <button onclick="toggleSource(${s.id})">切换</button>
      <button onclick="delSource(${s.id})">删除</button></div>`).join('') +
    `<button onclick="closeSources()">关闭</button>`;
  $('#source-modal').hidden = false;
}
window.toggleSource = async (id) => { await api.post(`/api/sources/${id}/toggle`); loadSources(); };
window.delSource = async (id) => { await api.delete(`/api/sources/${id}`); loadSources(); };
window.closeSources = () => $('#source-modal').hidden = true;

async function init() {
  $('#btn-sources').onclick = loadSources;
  $('#btn-collect').onclick = () => alert('收集由每日定时任务执行；如需立即收集请运行：uv run python -m idea_hub.cli collect');
  initSortable();
  await loadTargets();
  await loadBoard();
}
init();
```

Note: `api.delete` needs adding: `delete: (p) => fetch(p, {method:'DELETE'}).then(r=>r.json())`.

- [ ] **Step 5: Vendor SortableJS locally**

```bash
mkdir -p web/vendor
curl -sL https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js -o web/vendor/sortable.min.js
# verify: file starts with "/*! SortableJS" and size ~45-50KB
```

- [ ] **Step 6: Verify manually per the walkthrough checklist (Step 1)**

Run server: `uv run uvicorn idea_hub.server:app --port 8000` — note: `create_app("data/idea.db")` must be the default; export a module-level `app = create_app("data/idea.db")` at the bottom of `server.py` if uvicorn import fails.

- [ ] **Step 7: Commit**

```bash
git add web/
git commit -m "feat: kanban frontend with drag, drawer, sources modal"
```

---

### Task 8: Integration — cron jobs, startup script, end-to-end test, README

**Files:**
- Create: `scripts/start.sh`
- Create: `README.md`
- Append: `tests/test_e2e.py`
- Modify: `.gitignore` (nothing new needed)

**Interfaces:**
- Consumes: everything (Tasks 1-7)
- Produces:
  - `scripts/start.sh` — creates venv if missing, installs deps, starts uvicorn on 127.0.0.1:8000
  - Hermes cron definitions (documented in README; created via `cronjob` tool during implementation):
    - **Collect cron** (daily, e.g. 08:00): prompt → run `uv run python -m idea_hub.cli collect`; then `candidates`; for each candidate, write a draft markdown to a temp file, run `add-idea` (score via spec dimensions); then run `relate` pass: for new hot items, compare against archived tasks and re-score when related
    - **Execute cron** (every 15 min): `pending-executions`; for each pending task id: `next` (may print "queue empty" — skip), execute the task as a creator (produce `output.md` content per the task's idea.md), then `complete` or `fail`
  - `tests/test_e2e.py` — full pipeline with a fake hotlist source served by a tiny local HTTP server

- [ ] **Step 1: Write the failing end-to-end test** (`tests/test_e2e.py`)

```python
import json, pathlib, threading, subprocess, sys, time, http.server

class Handler(http.server.BaseHTTPRequestHandler):
    payload = {"data": [{"title": "热点1", "url": "http://h1", "hot": 88}]}
    def do_GET(self):
        body = json.dumps(self.payload).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

def test_full_pipeline(tmp_path):
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    db_path = str(tmp_path / "e2e.db")
    base = tmp_path
    conn = db.connect(db_path); db.init_schema(conn)
    models.create_target(conn, name="自媒体", description="d", score_dimensions="{}")
    models.activate_target(conn, 1)
    models.create_source(conn, type="hotlist", name="测试榜", url=f"http://127.0.0.1:{port}/")
    conn.close()
    def cli(*args):
        return subprocess.run([sys.executable, "-m", "idea_hub.cli", "--db", db_path, "--base", str(base), *args],
                              capture_output=True, text=True, cwd=pathlib.Path(__file__).parent.parent)
    r = cli("collect"); assert r.returncode == 0, r.stderr
    assert "collected=1" in r.stdout
    cand = cli("candidates")
    assert "热点1" in cand.stdout
    draft = tmp_path / "d.md"; draft.write_text("# 构思\n全文", encoding="utf-8")
    r = cli("add-idea", "--hot-item-id", "1", "--title", "写热点1", "--summary", "摘要",
            "--score", "8", "--dims", '{"热度":8}', "--detail-path", str(draft))
    assert r.returncode == 0, r.stderr
    r = cli("next"); assert r.returncode == 0, r.stderr
    out = tmp_path / "o.md"; out.write_text("# 产出\n正文", encoding="utf-8")
    r = cli("complete", "--task-id", "1", "--summary", "完成", "--output-path", str(out))
    assert r.returncode == 0, r.stderr
    conn = db.connect(db_path)
    t = models.get_task(conn, 1)
    assert t["status"] == "done"
    assert pathlib.Path(base, "outputs/tasks/1/output.md").exists()
    conn.close(); srv.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_e2e.py -v`
Expected: FAIL — `ModuleNotFoundError` (idea_hub not importable without venv) — if it passes, skip to Step 4.

- [ ] **Step 3: Implement `scripts/start.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -d .venv ]; then uv venv .venv; fi
uv pip install -r requirements.txt --quiet
mkdir -p data outputs backups
exec uv run uvicorn idea_hub.server:app --host 127.0.0.1 --port 8000
```

Add module-level app in `idea_hub/server.py` bottom: `app = create_app("data/idea.db")`.

- [ ] **Step 4: Run end-to-end test to verify it passes**

Run: `uv run pytest tests/test_e2e.py -v`
Expected: PASS

- [ ] **Step 5: Write `README.md`** — sections: 简介 (one paragraph, Chinese), 快速开始 (`bash scripts/start.sh`, open http://127.0.0.1:8000), 配置来源 (hotlist API items_path 说明 + RSS), 每日流程说明 (collect → candidates → add-idea/relate), 执行流程 (execute cron + 手动触发), 队列说明 (五队列 + 阈值 6 + 手动改分不移列), 目录结构, Hermes cron 定义 (两个 cron 的完整 prompt 文本, 创建命令示例).

- [ ] **Step 6: Commit**

```bash
git add scripts/ README.md tests/test_e2e.py idea_hub/server.py
git commit -m "feat: startup script, e2e test, README with cron definitions"
```

---

## Self-Review Notes (filled during planning)

**Spec coverage:**
- 架构/数据模型/流程 A/B/C → Tasks 1-8
- 双来源可配置 (hotlist + RSS) → Task 3 (`sources.type` check constraint, both fetchers)
- 混合评分 (固定维度 + 解释 + 手动修改) → Task 4 (`--dims` JSON + breakdown stored), Task 6 (PATCH score)
- 阈值 6 入列规则 → Task 1 (`create_task` status rule), Task 4 (`relate` re-score move)
- 五队列 + 拖拽 → Task 1 (status values), Task 7 (SortableJS)
- 定时自动 + 手动触发 → Task 5 (execute_requests) + Task 8 (crons)
- 产出物落盘 (idea.md / output.md) → Task 4 (`_write_draft`), Task 5 (`cmd_complete`)
- 关联信息重评分 → Task 4 (`relate` subcommand)
- 手动改分不移列 → Task 1 (update_task does not touch status)
- 备份保留 7 份 → Task 1 (`backup_db` prune)
- WAL + 错误处理 (来源失败跳过) → Task 1 (WAL), Task 3 (collect_all try/except)
- 并发守护 (waiting→in_progress 原子) → Task 1 (`try_start_task`)

**Placeholder scan:** no TBD/TODO/placeholders; every code step shows actual code.

**Type consistency:** `models.SCORE_THRESHOLD` used in Tasks 1 and 4; `try_start_task` signature identical across Tasks 1/5; CLI flags consistent (`--task-id`, `--hot-item-id`, `--score`, `--dims`, `--detail-path`); API routes match frontend calls (`/api/tasks/{id}/move`, `/api/tasks/{id}/execute`, `/api/sources/{id}/toggle`).
