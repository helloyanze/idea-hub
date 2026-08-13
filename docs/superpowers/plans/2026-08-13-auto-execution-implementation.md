# Idea Hub 自动执行调度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Idea Hub 在任务进入等待队列后自动调度执行并产出内容：cron 高频 tick 调度器 + 分层执行器（LLM API 直出 / Hermes agent）+ 轻量质检 + QQ/Web 通知 + 成本与健康监控。

**Architecture:** 云端 crontab 每 5 分钟触发无状态 `scheduler.py`（tick 只分发不等待，秒级退出），常规任务 spawn `executor.py` 子进程（幂等检查 → API 生成 → 质检重试 → 落盘 → complete → 通知），复杂任务 spawn hermes agent 子进程。SQLite 是唯一数据源，全部状态持久化；`next/complete/fail` 原子状态机复用。

**Tech Stack:** Python 3.11 / FastAPI / SQLite WAL / 原生 JS 前端 / pytest / Hermes CLI（hermes send）

## Global Constraints

- 规格文档：`docs/superpowers/specs/2026-08-13-auto-execution-design.md`（v4，已批准）
- 所有产出无 emoji；中文界面文案
- 现有 78 个测试必须保持通过；新功能全部 TDD
- pytest 运行：`.venv/Scripts/python.exe -m pytest -q`（Windows 本地）；云端为 `uv run pytest -q`
- DB 连接：`idea_hub.db.connect(path)` 返回 sqlite3.Row 连接；写库必须 `conn.commit()`
- 时间格式：ISO 字符串 `datetime.now().isoformat(timespec="seconds")`
- 不引入新第三方依赖（httpx 已有；文件锁用 `fcntl` 不可用于 Windows——本地测试用 `msvcrt`/`portalocker` 兼容层，见 Task 6）
- 本地开发不启调度（生产单一实例云端）
- 黑名单词表（可配置于 settings `ai_taste_blacklist`，默认）：首先/其次/最后/总的来说/值得注意的是/综上所述/众所周知/不言而喻/赋能/抓手/闭环
- `DEEPSEEK_API_KEY` 读取复用 `scorer._llm_key()`（支持 .env）

---

### Task 1: 数据库迁移 v2（schema 扩展 + notifications 表 + expire_at 回填）

**Files:**
- Modify: `idea_hub/db.py`（`_migrate` 内追加 v2 迁移）
- Modify: `idea_hub/models.py`（新增通知 CRUD 与统计函数）
- Test: `tests/test_migration_v2.py`（新建）

**Interfaces:**
- Produces: `models.create_notification(conn, *, task_id, type, title, body) -> int`
- Produces: `models.list_notifications(conn, unread_only=False, limit=50) -> list[dict]`
- Produces: `models.mark_notification_read(conn, notification_id) -> None`
- Produces: `models.mark_all_notifications_read(conn) -> None`
- Produces: `models.clear_old_notifications(conn, days=30) -> int`
- Produces: `models.daily_token_used(conn, date_str=None) -> int`
- Produces: `models.get_health(conn) -> dict`（last_scheduler_tick + 超时判断辅助）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_migration_v2.py
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
    conn.execute("INSERT INTO task_links (task_id, hot_item_id) VALUES (?,?)", (tid, hid))
    conn.commit()
    row = models.get_task(conn, tid)
    assert row["expire_at"] == "2026-08-14T00:00:00"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_migration_v2.py -v`
Expected: FAIL（列不存在 / notifications 表不存在 / create_notification 未定义）

- [ ] **Step 3: 实现迁移 v2**

`idea_hub/db.py` 的 `_migrate` 末尾追加：

```python
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
    conn.execute("""UPDATE tasks SET expire_at = (
            SELECT datetime(h.collected_at, '+' || s.ttl_hours || ' hours')
            FROM hot_items h JOIN sources s ON s.id = h.source_id
            WHERE h.id = tasks.hot_item_id AND s.ttl_hours IS NOT NULL)
        WHERE expire_at IS NULL AND hot_item_id IS NOT NULL""")
    conn.commit()
```

在 `_migrate` 末尾调用 `_migrate_v2(conn)`（检查幂等：`_migrate_v2` 内部以列存在与否判断，可重复执行）。

`idea_hub/models.py` 新增：

```python
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
        date_str = datetime.now().strftime("%Y-%m-%d")
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
```

注意：`models.py` 顶部需有 `from datetime import datetime`（检查现有 import，无则补）。

**同步扩展 models 签名（字段随迁移就绪，后续 Task 4/7 直接可用）：**

```python
def create_task(conn, *, title, idea_summary, target_id, hot_item_id=None,
                feasibility_score, score_breakdown, idea_path="",
                content_type="long", expire_at=None):
    # 现有逻辑不变，INSERT 语句增加 content_type/expire_at 两列
    ...
    cur = conn.execute(
        "INSERT INTO tasks (title, idea_summary, target_id, hot_item_id, status, "
        "feasibility_score, score_breakdown, idea_path, content_type, expire_at, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (title, idea_summary, target_id, hot_item_id, status, feasibility_score,
         score_breakdown, idea_path, content_type, expire_at, now, now))
    ...

def create_source(conn, *, type, name, url, enabled=True, items_path="data",
                  title_field="title", keywords="", ttl_hours=24):
    # INSERT 增加 ttl_hours 列
    ...
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_migration_v2.py -v`
Expected: 5 passed

- [ ] **Step 5: 回归 + 提交**

Run: `.venv/Scripts/python.exe -m pytest -q`（78+5 通过）
Commit: `git add idea_hub/db.py idea_hub/models.py tests/test_migration_v2.py && git commit -m "feat(db): migration v2 - auto-execution fields, notifications table, expire_at backfill"`

---

### Task 2: 通知模块（hermes send + notifications 表双写）

**Files:**
- Create: `idea_hub/notify.py`
- Test: `tests/test_notify.py`（新建）

**Interfaces:**
- Consumes: `models.create_notification`（Task 1）
- Produces: `notify.send(conn, *, task_id, type, title, body, qq_target=None) -> None`（双写；hermes send 失败仅记日志）
- Produces: `notify.qq_target() -> str`（读取 settings `qq_target`，默认空串=不推送）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_notify.py
"""通知模块：hermes send 子进程调用（mock）+ notifications 表双写。"""
import sqlite3
from pathlib import Path
from unittest.mock import patch
from idea_hub import db, models, notify

def test_send_writes_notification_and_calls_send(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    calls = []
    def fake_run(cmd, capture_output=True, text=True, timeout=30):
        calls.append(cmd)
        class R: returncode = 0
        return R()
    monkeypatch.setattr(notify.subprocess, "run", fake_run)
    notify.send(conn, task_id=1, type="done", title="完成", body="摘要",
                qq_target="qq:123")
    rows = models.list_notifications(conn)
    assert len(rows) == 1 and rows[0]["type"] == "done"
    assert any("hermes" in str(c) and "qq:123" in str(c) for c in calls)

def test_send_without_target_skips_send(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    called = []
    monkeypatch.setattr(notify.subprocess, "run", lambda *a, **k: called.append(a))
    notify.send(conn, task_id=None, type="scheduler", title="t", body="b", qq_target=None)
    assert called == []

def test_send_failure_does_not_raise(tmp_path, monkeypatch):
    conn = db.connect(str(tmp_path / "t.db"))
    def boom(*a, **k):
        raise FileNotFoundError("hermes not installed")
    monkeypatch.setattr(notify.subprocess, "run", boom)
    notify.send(conn, task_id=1, type="done", title="t", body="b", qq_target="qq:1")
    assert len(models.list_notifications(conn)) == 1  # 表已写，不抛异常
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notify.py -v`
Expected: FAIL（notify 模块不存在）

- [ ] **Step 3: 实现 notify.py**

```python
"""通知模块：hermes send（QQ 推送）+ notifications 表双写。"""
import subprocess
from idea_hub import models


def send(conn, *, task_id, type, title, body, qq_target=None):
    """双写通知：notifications 表必写；qq_target 非空时调 hermes send。
    hermes send 失败只记日志（notifications 表兜底，Web 端可见），不抛异常。"""
    if qq_target is None:
        qq_target = models.get_setting(conn, "qq_target", "")
    nid = models.create_notification(conn, task_id=task_id, type=type,
                                     title=title, body=body)
    if qq_target:
        try:
            msg = f"{title}\n{body}"
            subprocess.run(["hermes", "send", "--to", qq_target, "--quiet", msg],
                           capture_output=True, text=True, timeout=30)
        except Exception as exc:  # 子进程缺失/超时/非零退出均不阻断
            print(f"[notify] hermes send failed (notification {nid} kept in DB): {exc}")
    return nid
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_notify.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

Commit: `git add idea_hub/notify.py tests/test_notify.py && git commit -m "feat(notify): dual-channel notifications (hermes send + DB table)"`

---

### Task 3: 内容类型模板与质检 prompt（prompts.py）

**Files:**
- Create: `idea_hub/prompts.py`
- Test: `tests/test_prompts.py`（新建）

**Interfaces:**
- Produces: `prompts.CONTENT_TEMPLATES: dict[str, str]`（short/long/video_script 六段式模板）
- Produces: `prompts.build_generation_prompt(task: dict, target: dict, hot_titles: list[str], redo_note: str|None) -> str`
- Produces: `prompts.build_qa_prompt(content: str, content_type: str) -> str`
- Produces: `prompts.build_regenerate_prompt(base: str, qa: dict) -> str`
- Produces: `prompts.check_ai_taste(content: str, blacklist: list[str]) -> list[str]`（命中黑名单词列表，词边界处理）
- Produces: `prompts.parse_llm_json(raw: str) -> dict`（容错解析：code fence / 前后杂文本）
- Produces: `prompts.QA_SCHEMA_EXAMPLE: str`（质检输出 JSON 示例，注入 QA prompt）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_prompts.py
"""内容模板 / 质检 prompt / AI 味规则层 / JSON 容错解析。"""
import idea_hub.prompts as P

TASK = {"title": "测试标题", "idea_summary": "摘要", "content_type": "long",
        "redo_note": None, "idea_path": ""}
TARGET = {"name": "自媒体内容", "description": "面向普通读者的深度文章"}

def test_templates_have_six_sections():
    for ct in ("short", "long", "video_script"):
        t = P.CONTENT_TEMPLATES[ct]
        assert "角色设定" in t and "写作原则" in t and "结构要求" in t
        assert "风格与语言" in t and "禁忌清单" in t

def test_build_generation_prompt_injects_context():
    p = P.build_generation_prompt(TASK, TARGET, ["热点A", "热点B"], None)
    assert "测试标题" in p and "自媒体内容" in p and "热点A" in p
    assert "JSON" in p  # 输出格式要求

def test_redo_note_injected():
    p = P.build_generation_prompt(TASK, TARGET, [], "请写得更口语化")
    assert "请写得更口语化" in p

def test_qa_prompt_contains_schema():
    p = P.build_qa_prompt("正文内容", "long")
    assert '"pass"' in p and "issues" in p and "quote" in p

def test_ai_taste_hits_and_boundary():
    hits = P.check_ai_taste("首先我们要注意，最后总结一下。", ["首先", "其次", "最后"])
    assert set(hits) == {"首先", "最后"}
    # 词边界：'最后' 作为时间状语仍命中（黑名单为模板词策略），但普通词不误伤
    assert P.check_ai_taste("我们今天去爬山。", ["首先", "最后"]) == []

def test_parse_llm_json_tolerant():
    raw = '```json\n{"title": "t", "content": "c"}\n```'
    assert P.parse_llm_json(raw) == {"title": "t", "content": "c"}
    assert P.parse_llm_json('{"a": 1} trailing text') == {"a": 1}
    assert P.parse_llm_json("no json") == {}

def test_regenerate_prompt_includes_fixes():
    qa = {"pass": False, "issues": [{"type": "style", "quote": "值得注意的是",
                                     "problem": "模板词", "fix": "删除或改写"}],
          "suggestions": "整体口语化"}
    p = P.build_regenerate_prompt("原生成 prompt", qa)
    assert "值得注意的是" in p and "整体口语化" in p and "可忽略" in p
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prompts.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 prompts.py**

```python
"""内容类型模板、质检 prompt 与 AI 味规则层。

模板结构为六段式（角色设定/写作原则/结构要求/风格与语言/标题技巧/禁忌清单），
调研自 Article-Transcription-Assistant final_polishers 与 multi-agent WriterAgent
（见 docs/RESEARCH_COMPETITORS.md）。
"""
import json
import re

# ---- 六段式内容类型模板 ----
CONTENT_TEMPLATES = {
    "short": """你是自媒体短文写手，擅长微博/知乎风格的短内容。

## 写作原则
- 观点鲜明，200-500 字，可直接发布
- 口语化，拒绝公文腔和空话

## 结构要求
- 必须有标题和正文；正文第一句即观点或钩子
- 单段不超过 5 行

## 风格与语言
- 使用具体案例或数据支撑观点（数据须来自提供的热点信息）
- 避免模板词：首先/其次/最后/总的来说/值得注意的是/综上所述/众所周知/不言而喻/赋能/抓手/闭环

## 标题技巧
- 给出 1 个标题

## 禁忌清单
- 不堆术语、不写无观点资讯、不用震惊体、不编造数据""",

    "long": """你是资深公众号长文作者，写作 1000-3000 字的深度内容。

## 写作原则
- 移动端优先：每段 3-4 行，段间空行
- 信息密度高：每段都有信息增量，不注水
- 口语化但不失深度

## 结构要求
- 标题 + 开头钩子（50 字内制造冲突或好奇）+ 2-3 个论点（每论点=观点+案例/数据+小结）+ 结尾金句
- 必须有二级小标题分隔论点

## 风格与语言
- 多用类比和比喻，关键数据用阿拉伯数字
- 避免模板词：首先/其次/最后/总的来说/值得注意的是/综上所述/众所周知/不言而喻/赋能/抓手/闭环

## 标题技巧
- 提供 3 个备选标题：A 悬念型 / B 观点型 / C 利益型

## 禁忌清单
- 不写超过 3000 字、不堆砌术语、不写无观点的资讯搬运、不用震惊/重磅等低质词
- 涉及具体数字/日期/引用时必须能由提供的热点信息支撑，否则改为模糊表述""",

    "video_script": """你是短视频脚本作者，为口播类短视频撰写脚本。

## 写作原则
- 按分镜组织，节奏紧凑，总时长 1-3 分钟（约 250-750 字口播稿）
- 开场 5 秒必须有钩子（悬念/反常识/直接提问）

## 结构要求
- 脚本必须包含：开场钩子 → 主体分镜（每个分镜含画面提示 + 口播词）→ 结尾引导（关注/评论/收藏）
- 每个分镜标注时长（如 0:00-0:05）

## 风格与语言
- 口播词口语化、短句、有情绪起伏；画面提示用【】标注
- 避免模板词：首先/其次/最后/总的来说/值得注意的是/综上所述/众所周知/不言而喻/赋能/抓手/闭环

## 标题技巧
- 给出视频标题 1 个

## 禁忌清单
- 不写长难句、不用书面语堆砌、不编造数据、不用震惊体""",
}

QA_SCHEMA_EXAMPLE = """{
  "pass": true,
  "issues": [
    {"type": "fact|logic|structure|style", "quote": "原句", "problem": "问题", "fix": "怎么改"}
  ],
  "suggestions": "改进方向"
}"""


def build_generation_prompt(task, target, hot_titles, redo_note):
    tpl = CONTENT_TEMPLATES.get(task.get("content_type") or "long",
                                CONTENT_TEMPLATES["long"])
    hot_block = "\n".join(f"- {t}" for t in hot_titles) or "（无）"
    redo_block = f"\n用户修改意见（必须落实）：{redo_note}" if redo_note else ""
    return f"""{tpl}

## 本次任务
- 任务标题：{task['title']}
- 构思摘要：{task.get('idea_summary', '')}
- 目标模式：{target['name']}（{target.get('description', '')}）
- 关联热点素材：
{hot_block}
{redo_block}

## 输出格式
只输出 JSON：{{"title": "最终标题", "content": "正文（Markdown）", "word_count": 实际字数}}
不要输出 JSON 以外的任何内容。"""


def build_qa_prompt(content, content_type):
    return f"""你是严格的内容质检员。检查下面这篇{content_type}内容，输出 JSON。

检查维度：
1. 字数达标（短文 200-500 字；长文 1000-3000 字；视频脚本 250-750 字）
2. AI 味：模板化表达、排比泛滥、套话开头（首先/其次/总的来说/值得注意的是等）
3. 事实性错误：包含具体数字/日期/引用但无来源支撑；推测被写成事实
4. 结构完整性：短文=标题+正文；长文=标题+分段+小标题+结尾；视频脚本=开场钩子+分镜+时长标注

输出格式（严格 JSON，不要其他文字）：
{QA_SCHEMA_EXAMPLE}

pass=false 时 issues 必须包含具体问题，每条含 quote（原句/位置）、problem、fix（可执行修改动作）。

<content>
{content}
</content>"""


def build_regenerate_prompt(base_prompt, qa):
    issues = "\n".join(
        f"- 位置「{i.get('quote', '')}」：{i.get('problem', '')}；建议：{i.get('fix', '')}"
        for i in qa.get("issues", []))
    sug = qa.get("suggestions", "")
    return f"""{base_prompt}

上一版存在以下问题，请修正（如某条与事实不符可忽略）：
{issues}
整体改进方向：{sug}

输出格式不变（只输出 JSON）。"""


def check_ai_taste(content, blacklist):
    """本地规则层：命中黑名单模板词返回命中列表（0 token）。
    黑名单为"模板词"策略：常见套话直接命中；普通语境用词不在黑名单内。"""
    hits = []
    for w in blacklist:
        if w and w in content:
            hits.append(w)
    return hits


def parse_llm_json(raw):
    """容错解析 LLM JSON 输出：剥 code fence、截取首个 {...} 块。"""
    if not raw:
        return {}
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m2 = re.search(r"\{.*\}", text, re.S)
        if m2:
            try:
                return json.loads(m2.group(0))
            except json.JSONDecodeError:
                return {}
        return {}
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_prompts.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

Commit: `git add idea_hub/prompts.py tests/test_prompts.py && git commit -m "feat(prompts): content-type six-section templates, QA prompt, AI-taste rule layer"`

---

### Task 4: 常规执行器 executor.py（核心）

**Files:**
- Create: `idea_hub/executor.py`
- Modify: `idea_hub/scorer.py`（导出 `_llm_key` 为公共 `llm_key()`；不破坏现有调用）
- Test: `tests/test_executor.py`（新建）

**Interfaces:**
- Consumes: Task 1（models）、Task 3（prompts）
- Produces: `executor.execute_task(db_path, task_id, base_path) -> int`（0=done, 1=failed, 2=idempotent-done）
- Produces: `executor.call_llm(prompt, *, timeout=120) -> tuple[str, int]`（返回 (文本, 本次 token 数)；超时抛 TimeoutError）
- Produces: `executor.run_qa(content, content_type, timeout=60) -> dict`（质检 JSON，解析失败视为不通过）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_executor.py
"""常规执行器：幂等/生成/质检重试/规则层/版本保留/失败计数。"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from idea_hub import db, executor, models, prompts

def _setup(tmp_path: Path) -> tuple[sqlite3.Connection, int]:
    conn = db.connect(str(tmp_path / "t.db"))
    tid = models.create_task(conn, title="测试任务", idea_summary="摘要",
                             target_id=1, feasibility_score=9,
                             score_breakdown="{}", content_type="long")
    d = Path(tmp_path) / "outputs" / "tasks" / str(tid)
    d.mkdir(parents=True)
    (d / "idea.md").write_text("# 构思\n深度内容", encoding="utf-8")
    return conn, tid

def _fake_call_llm(returns):
    """返回 (content, tokens) 的迭代器模拟。"""
    it = iter(returns)
    def f(prompt, *, timeout=120):
        return next(it)
    return f

def test_idempotent_skip_when_output_exists(tmp_path):
    conn, tid = _setup(tmp_path)
    d = Path(tmp_path) / "outputs" / "tasks" / str(tid)
    (d / "output.md").write_text("已有产出", encoding="utf-8")
    with patch.object(executor, "call_llm", side_effect=AssertionError("不应调用 API")):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 2
    assert models.get_task(conn, tid)["status"] == "done"

def test_happy_path_done_and_versioning(tmp_path):
    conn, tid = _setup(tmp_path)
    d = Path(tmp_path) / "outputs" / "tasks" / str(tid)
    (d / "output.md").write_text("旧版", encoding="utf-8")  # 模拟打回重做有旧版
    payload = json.dumps({"title": "新标题", "content": "正文内容" * 100,
                          "word_count": 300}, ensure_ascii=False)
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    calls = [("llm_gen", 1000), ("qa", 300)]
    def fake(prompt, *, timeout=120):
        kind = calls.pop(0)
        return (payload if kind[0] == "llm_gen" else qa_ok), kind[1]
    with patch.object(executor, "call_llm", side_effect=fake):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    task = models.get_task(conn, tid)
    assert task["status"] == "done"
    assert task["token_used"] == 1300
    assert task["fail_count"] == 0
    assert (d / "output.md").exists()
    assert (d / "output_v1.md").read_text(encoding="utf-8") == "旧版"

def test_qa_fail_then_regenerate_then_pass(tmp_path):
    conn, tid = _setup(tmp_path)
    payload_ok = json.dumps({"title": "t", "content": "好内容" * 100, "word_count": 300})
    payload_v2 = json.dumps({"title": "t2", "content": "修正后内容" * 100, "word_count": 300})
    qa_fail = json.dumps({"pass": False,
                          "issues": [{"type": "style", "quote": "值得注意的是",
                                      "problem": "模板词", "fix": "删除"}],
                          "suggestions": "口语化"})
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload_ok, 1000), (qa_fail, 300), (payload_v2, 1000), (qa_ok, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    task = models.get_task(conn, tid)
    assert task["status"] == "done"
    assert task["token_used"] == 2600  # 两次生成 + 两次质检

def test_qa_fail_twice_fails_task(tmp_path):
    conn, tid = _setup(tmp_path)
    payload = json.dumps({"title": "t", "content": "差内容" * 100, "word_count": 300})
    qa_bad = json.dumps({"pass": False,
                         "issues": [{"type": "structure", "quote": "开头",
                                     "problem": "无钩子", "fix": "加钩子"}],
                         "suggestions": "重写"})
    seq = [(payload, 1000), (qa_bad, 300), (payload, 1000), (qa_bad, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 1
    task = models.get_task(conn, tid)
    assert task["status"] == "waiting"  # fail 退回
    assert task["fail_count"] == 1
    assert task["last_fail_reason"] and "质检" in task["last_fail_reason"]

def test_ai_taste_rule_triggers_regenerate(tmp_path):
    conn, tid = _setup(tmp_path)
    payload_bad = json.dumps({"title": "t", "content": "首先，值得注意的是，本内容很模板。",
                              "word_count": 100})
    payload_ok = json.dumps({"title": "t", "content": "自然口语的好内容" * 100, "word_count": 300})
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload_bad, 1000), (payload_ok, 1000), (qa_ok, 300)]
    with patch.object(executor, "call_llm", side_effect=lambda p, **k: seq.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0  # 规则层命中 → 重生成 → 通过
    assert models.get_task(conn, tid)["status"] == "done"

def test_call_llm_timeout_raises():
    with patch("httpx.post", side_effect=TimeoutError("timeout")):
        try:
            executor.call_llm("prompt", timeout=1)
            assert False, "应抛异常"
        except TimeoutError:
            pass
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_executor.py -v`
Expected: FAIL（executor 不存在）

- [ ] **Step 3: 实现 executor.py**

```python
"""常规任务执行器（独立子进程运行）：幂等 → 生成 → 质检重试 → 落盘 → complete。"""
import json
import pathlib
import re
import sys

import httpx

from idea_hub import db, models, notify, prompts
from idea_hub.scorer import LLM_URL, LLM_MODEL, llm_key

LLM_TIMEOUT = 120
QA_TIMEOUT = 60
MAX_GENERATE_ATTEMPTS = 2  # 首次 + 质检重试 1 次


def call_llm(prompt, *, timeout=LLM_TIMEOUT):
    """调 DeepSeek API，返回 (文本, 本次 token 数)。超时抛 TimeoutError。"""
    key = llm_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    resp = httpx.post(LLM_URL, timeout=timeout,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": LLM_MODEL, "messages": [
                          {"role": "user", "content": prompt}],
                          "temperature": 0.7,
                          "response_format": {"type": "json_object"}})
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    tokens = data.get("usage", {}).get("total_tokens", 0)
    return content, tokens


def run_qa(content, content_type, timeout=QA_TIMEOUT):
    """质检。返回 (qa_dict, tokens)。解析失败视为不通过。"""
    raw, tokens = call_llm(prompts.build_qa_prompt(content, content_type), timeout=timeout)
    qa = prompts.parse_llm_json(raw)
    if not qa:
        qa = {"pass": False, "issues": [{"type": "structure", "quote": "",
                                         "problem": "质检输出解析失败",
                                         "fix": "重新生成"}], "suggestions": ""}
    return qa, tokens


def _version_output(base_path, task_id):
    """output.md 已存在则按最大版本号+1 改名保留旧版；返回新 output.md 路径。"""
    d = pathlib.Path(base_path) / "outputs" / "tasks" / str(task_id)
    d.mkdir(parents=True, exist_ok=True)
    target = d / "output.md"
    if target.exists():
        nums = []
        for p in d.glob("output_v*.md"):
            m = re.match(r"output_v(\d+)\.md", p.name)
            if m:
                nums.append(int(m.group(1)))
        n = (max(nums) + 1) if nums else 1
        target.rename(d / f"output_v{n}.md")
    return target


def _complete_task(conn, task_id, summary, output_path, token_used):
    rel = str(pathlib.Path("outputs") / "tasks" / str(task_id) / "output.md").replace("\\", "/")
    models.update_task(conn, task_id, ai_summary=summary, output_path=rel,
                       token_used=token_used, fail_count=0, last_fail_reason=None)
    models.move_task(conn, task_id, "done")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (task_id,))
    conn.commit()


def _fail_task(conn, task_id, reason, token_used):
    task = models.get_task(conn, task_id)
    models.update_task(conn, task_id,
                       fail_count=(task["fail_count"] or 0) + 1,
                       last_fail_reason=reason,
                       token_used=(task["token_used"] or 0) + token_used)
    models.move_task(conn, task_id, "waiting")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (task_id,))
    conn.commit()


def _summary_from(payload: dict, content: str) -> str:
    """完成摘要：优先用 LLM 输出的 title，否则截取正文首 60 字。"""
    title = (payload.get("title") or "").strip()
    if title:
        return title
    plain = re.sub(r"\s+", " ", content).strip()
    return plain[:60]


def execute_task(db_path, task_id, base_path):
    """执行单个常规任务。返回 0=done, 1=failed, 2=幂等直接完成。"""
    conn = db.connect(db_path)
    try:
        task = models.get_task(conn, task_id)
        if task is None:
            print(f"error: task {task_id} not found", file=sys.stderr)
            return 1
        if task["status"] != "in_progress":
            print(f"skip: task {task_id} status={task['status']}", file=sys.stderr)
            return 1
        d = pathlib.Path(base_path) / "outputs" / "tasks" / str(task_id)
        if (d / "output.md").exists() and (d / "output.md").stat().st_size > 0:
            # 幂等：产出已存在（如 complete 网络中断后重跑）→ 直接完成
            _complete_task(conn, task_id, task.get("ai_summary") or "已完成", d / "output.md",
                           task.get("token_used") or 0)
            conn.close()
            return 2

        target = models.get_active_target(conn) or {"name": "默认", "description": ""}
        hot_titles = [r["title"] for r in conn.execute(
            "SELECT h.title FROM hot_items h JOIN task_links tl ON tl.hot_item_id=h.id "
            "WHERE tl.task_id=?", (task_id,)).fetchall()]
        base_prompt = prompts.build_generation_prompt(task, target, hot_titles,
                                                      task.get("redo_note"))
        total_tokens = 0
        payload = {}
        content = ""
        for attempt in range(MAX_GENERATE_ATTEMPTS):
            raw, tokens = call_llm(base_prompt)
            total_tokens += tokens
            payload = prompts.parse_llm_json(raw)
            content = (payload.get("content") or "").strip()
            if not content:
                # 输出非法：视为一次失败，直接进入重试分支
                qa = {"pass": False,
                      "issues": [{"type": "structure", "quote": "", "problem": "LLM 输出为空或非法",
                                  "fix": "重新生成"}], "suggestions": ""}
            else:
                # AI 味规则层（0 token）：命中模板词 → 直接重生成
                blacklist = (models.get_setting(conn, "ai_taste_blacklist",
                                                "首先,其次,最后,总的来说,值得注意的是,综上所述,众所周知,不言而喻,赋能,抓手,闭环")
                             or "").split(",")
                hits = prompts.check_ai_taste(content, blacklist)
                if hits:
                    qa = {"pass": False,
                          "issues": [{"type": "style", "quote": hits[0],
                                      "problem": f"命中模板词：{'、'.join(hits)}",
                                      "fix": "删除或改写为自然表达"}],
                          "suggestions": "去除模板化表达"}
                else:
                    qa, qa_tokens = run_qa(content, task.get("content_type") or "long")
                    total_tokens += qa_tokens
            if qa.get("pass"):
                break
            if attempt == MAX_GENERATE_ATTEMPTS - 1:
                reason = "质检未通过: " + json.dumps(qa, ensure_ascii=False)[:200]
                _fail_task(conn, task_id, reason, total_tokens)
                conn.close()
                return 1
            base_prompt = prompts.build_regenerate_prompt(base_prompt, qa)

        out = _version_output(base_path, task_id)
        out.write_text(content, encoding="utf-8")
        summary = _summary_from(payload, content)
        _complete_task(conn, task_id, summary, out, total_tokens)
        # 预算完成时复核（通知由调度/前端轮询兜底，此处只记录）
        conn.close()
        return 0
    except Exception as exc:
        try:
            _fail_task(conn, task_id, f"执行异常: {exc}", 0)
        except Exception:
            pass
        conn.close()
        return 1
```

`scorer.py` 修改：`_llm_key` 保持，新增别名 `llm_key = _llm_key`（放 `_llm_key` 定义后一行）；确认 `LLM_URL`/`LLM_MODEL` 为模块级常量（已是）。

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_executor.py -v`
Expected: 7 passed（若 `response_format` 不被 mock 拦截无碍——httpx.post 已 mock）

- [ ] **Step 5: 回归 + 提交**

Run: `.venv/Scripts/python.exe -m pytest -q`
Commit: `git add idea_hub/executor.py idea_hub/scorer.py tests/test_executor.py && git commit -m "feat(executor): LLM executor with idempotency, QA gate + regenerate, AI-taste rule layer, versioning"`

---

### Task 5: CLI 扩展（complete --token-used、fail 计数、execute-auto 入口）

**Files:**
- Modify: `idea_hub/cli.py`
- Test: `tests/test_cli_execution.py`（新建）

**Interfaces:**
- Consumes: Task 4 `executor.execute_task`
- Produces: CLI 子命令 `execute-auto --task-id N [--db PATH] [--base PATH]`（调用 executor，退出码 0/1/2）
- Produces: `complete --token-used N`（默认 0；成功时 fail_count 清零、token_used 累加）
- Produces: `fail`（fail_count+1、last_fail_reason 写入；沿用退回 waiting）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli_execution.py
"""CLI：execute-auto 入口、complete --token-used、fail 计数。"""
import json
import subprocess
import sys
from pathlib import Path
from idea_hub import db, models

def _run_cli(tmp_path: Path, *args):
    return subprocess.run([sys.executable, "-m", "idea_hub.cli", "--db",
                           str(tmp_path / "t.db"), *args],
                          capture_output=True, text=True, cwd=str(tmp_path))

def _mk_task(tmp_path: Path) -> int:
    conn = db.connect(str(tmp_path / "t.db"))
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    conn.close()
    return tid

def test_complete_with_token_used_and_reset(tmp_path):
    tid = _mk_task(tmp_path)
    out = tmp_path / "out.md"
    out.write_text("内容", encoding="utf-8")
    r = _run_cli(tmp_path, "next", "--task-id", str(tid))
    assert r.returncode == 0
    conn = db.connect(str(tmp_path / "t.db"))
    models.update_task(conn, tid, fail_count=2)  # 模拟失败计数
    conn.close()
    r = _run_cli(tmp_path, "complete", "--task-id", str(tid),
                 "--summary", "完成", "--output-path", str(out), "--token-used", "777")
    assert r.returncode == 0
    conn = db.connect(str(tmp_path / "t.db"))
    task = models.get_task(conn, tid)
    assert task["status"] == "done" and task["token_used"] == 777
    assert task["fail_count"] == 0  # 成功清零

def test_fail_increments_count_and_reason(tmp_path):
    tid = _mk_task(tmp_path)
    _run_cli(tmp_path, "next", "--task-id", str(tid))
    r = _run_cli(tmp_path, "fail", "--task-id", str(tid), "--reason", "测试失败")
    assert r.returncode == 0
    conn = db.connect(str(tmp_path / "t.db"))
    task = models.get_task(conn, tid)
    assert task["status"] == "waiting" and task["fail_count"] == 1
    assert task["last_fail_reason"] == "测试失败"

def test_execute_auto_cli(tmp_path):
    tid = _mk_task(tmp_path)
    _run_cli(tmp_path, "next", "--task-id", str(tid))
    with __import__("unittest.mock", fromlist=["patch"]).patch(
            "idea_hub.executor.execute_task", return_value=0) as m:
        r = _run_cli(tmp_path, "execute-auto", "--task-id", str(tid))
    assert r.returncode == 0
    m.assert_called_once()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_execution.py -v`
Expected: FAIL（--token-used 未定义 / execute-auto 未定义）

- [ ] **Step 3: 实现 CLI 扩展**

`idea_hub/cli.py`：

```python
def cmd_execute_auto(args):
    """调常规执行器（子进程模式）。退出码 0=done 1=failed 2=幂等完成。"""
    rc = executor.execute_task(args.db, args.task_id, args.base)
    sys.exit(rc)
```

`cmd_complete` 修改（`models.update_task` 前增加 fail_count 清零与 token 累加）：

```python
def cmd_complete(args):
    conn = _conn(args)
    task = models.get_task(conn, args.task_id)
    if task is None:
        print(f"error: task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    content = pathlib.Path(args.output_path).read_text(encoding="utf-8")
    d = pathlib.Path(args.base) / "outputs" / "tasks" / str(args.task_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "output.md"
    p.write_text(content, encoding="utf-8")
    rel = str(pathlib.Path("outputs") / "tasks" / str(args.task_id) / "output.md").replace("\\", "/")
    token_used = (task.get("token_used") or 0) + getattr(args, "token_used", 0)
    models.update_task(conn, args.task_id, ai_summary=args.summary, output_path=rel,
                       token_used=token_used, fail_count=0, last_fail_reason=None)
    models.move_task(conn, args.task_id, "done")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (args.task_id,))
    conn.commit()
    print("done")
```

`cmd_fail` 修改（写入 fail_count + last_fail_reason，保留 notes 记录）：

```python
def cmd_fail(args):
    conn = _conn(args)
    task = models.get_task(conn, args.task_id)
    if task is None:
        print(f"error: task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    fail_count = (task.get("fail_count") or 0) + 1
    models.update_task(conn, args.task_id,
                       notes=f"{task['notes']}\n[失败] {args.reason}".strip(),
                       fail_count=fail_count, last_fail_reason=args.reason)
    models.move_task(conn, args.task_id, "waiting")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (args.task_id,))
    conn.commit()
    print("waiting")
```

`main()` 中注册：

```python
    pe = sub.add_parser("execute-auto")
    pe.add_argument("--task-id", type=int, required=True)
    pe.set_defaults(func=cmd_execute_auto)
    pc.add_argument("--token-used", type=int, default=0,
                    help="本次执行消耗 token（成本统计，默认 0）")
```

顶部 import：`from idea_hub import executor`。

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cli_execution.py -v`
Expected: 3 passed

- [ ] **Step 5: 回归 + 提交**

Run: `.venv/Scripts/python.exe -m pytest -q`
Commit: `git add idea_hub/cli.py tests/test_cli_execution.py && git commit -m "feat(cli): execute-auto entry, complete --token-used, fail counting"`

---

### Task 6: 调度器 scheduler.py（tick 全逻辑）

**Files:**
- Create: `idea_hub/scheduler.py`
- Test: `tests/test_scheduler.py`（新建）

**Interfaces:**
- Consumes: Task 1（models）、Task 4（executor 子进程）
- Produces: `scheduler.tick(db_path, base_path) -> dict`（返回本 tick 动作摘要：{"claimed": [ids], "recovered": [ids], "expired": [ids], "skipped_budget": bool}）
- Produces: `scheduler.main()`（CLI 入口：`python -m idea_hub.scheduler --db ... --base ...`）
- Produces: `scheduler._acquire_lock(path) -> object|None`（Windows 兼容文件锁）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_scheduler.py
"""调度器 tick：预算/回收/过期/并发/领取/插队/分发。"""
import sqlite3
from pathlib import Path
from unittest.mock import patch
from idea_hub import db, models, scheduler

def _mk(conn, **kw) -> int:
    return models.create_task(conn, title=kw.get("title", "t"),
                              idea_summary="s", target_id=1, feasibility_score=9,
                              score_breakdown="{}", **{k: v for k, v in kw.items()
                                                      if k in ("content_type", "expire_at")})

def _waiting(tmp_path: Path, conn: sqlite3.Connection, n=1) -> list[int]:
    ids = []
    for i in range(n):
        tid = _mk(conn, title=f"t{i}")
        models.move_task(conn, tid, "waiting")
        ids.append(tid)
    conn.commit()
    return ids

def test_tick_claims_waiting_and_spawns(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    tid = _waiting(tmp_path, conn)[0]
    with patch("subprocess.Popen") as pop, \
         patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert r["claimed"] == [tid]
    assert models.get_task(conn, tid)["status"] == "in_progress"
    pop.assert_called_once()

def test_budget_skip(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    tid = _waiting(tmp_path, conn)[0]
    models.set_setting(conn, "max_daily_tokens", "10")
    tid2 = _mk(conn, title="big")
    models.update_task(conn, tid2, token_used=100)
    conn.commit()
    with patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert r["skipped_budget"] is True
    assert models.get_task(conn, tid)["status"] == "waiting"  # 未被领取

def test_auto_execute_off_still_recovers_and_claims_manual(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    tid = _waiting(tmp_path, conn)[0]
    models.set_setting(conn, "auto_execute", "0")
    # 卡死任务（in_progress 超时）
    stale = _mk(conn, title="stale")
    conn.execute("UPDATE tasks SET status='in_progress', "
                 "updated_at=datetime('now','-120 minutes') WHERE id=?", (stale,))
    # 手动插队
    conn.execute("INSERT INTO execute_requests (task_id) VALUES (?)", (tid,))
    conn.commit()
    with patch("idea_hub.scheduler._acquire_lock", return_value=object()), \
         patch("subprocess.Popen"):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert stale in r["recovered"]          # 卡死回收不受 auto_execute 影响
    assert models.get_task(conn, stale)["status"] == "waiting"
    assert tid in r["claimed"]              # 插队仍被处理
    assert models.get_task(conn, tid)["status"] == "in_progress"

def test_expired_archived(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    tid = _waiting(tmp_path, conn)[0]
    conn.execute("UPDATE tasks SET expire_at='2000-01-01T00:00:00' WHERE id=?", (tid,))
    conn.commit()
    with patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert tid in r["expired"]
    assert models.get_task(conn, tid)["status"] == "archived"

def test_max_concurrent_blocks(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    ids = _waiting(tmp_path, conn, n=2)
    # 模拟已有 1 个 in_progress（并发上限 1）
    conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (ids[0],))
    conn.commit()
    with patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert r["claimed"] == []
    assert models.get_task(conn, ids[1])["status"] == "waiting"

def test_lock_exclusive(tmp_path):
    l1 = scheduler._acquire_lock(str(tmp_path / "sched.lock"))
    l2 = scheduler._acquire_lock(str(tmp_path / "sched.lock"))
    assert l1 is not None
    assert l2 is None  # 第二个拿不到锁
    l1.release()
    l3 = scheduler._acquire_lock(str(tmp_path / "sched.lock"))
    assert l3 is not None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler.py -v`
Expected: FAIL（scheduler 不存在）

- [ ] **Step 3: 实现 scheduler.py**

```python
"""无状态调度器（cron 每 5 分钟触发，tick 只分发不等待）。

tick 顺序：健康心跳 → 预算检查 → auto_execute 门控（只影响自动领取）
→ 卡死回收 → 过期归档 → 并发检查 → 领取（插队优先）→ 分发（spawn 子进程）→ 通知清理。
"""
import pathlib
import subprocess
import sys
import time
from datetime import datetime

from idea_hub import db, models


def _acquire_lock(lock_path):
    """跨平台文件锁：Unix fcntl / Windows msvcrt。拿不到返回 None。"""
    import os
    f = open(lock_path, "w")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()
        return f
    except (OSError, IOError):
        f.close()
        return None


def _recover_stale(conn):
    """卡死回收：in_progress 且心跳超时 → fail 退回 waiting。返回回收 id 列表。"""
    simple_min = int(models.get_setting(conn, "stale_simple_min", "5"))
    complex_min = int(models.get_setting(conn, "stale_complex_min", "60"))
    out = []
    rows = conn.execute("SELECT id, is_complex, updated_at FROM tasks "
                        "WHERE status='in_progress'").fetchall()
    for r in rows:
        limit = complex_min if r["is_complex"] else simple_min
        try:
            updated = datetime.fromisoformat(r["updated_at"])
            stale = (datetime.now() - updated).total_seconds() > limit * 60
        except (TypeError, ValueError):
            stale = True  # 时间解析失败按卡死处理
        if stale:
            task = dict(r)
            task["fail_count"] = (task.get("fail_count") or 0) + 1
            models.update_task(conn, r["id"],
                               fail_count=(models.get_task(conn, r["id"])["fail_count"] or 0) + 1,
                               last_fail_reason="执行超时回收")
            models.move_task(conn, r["id"], "waiting")
            out.append(r["id"])
    conn.commit()
    return out


def _archive_expired(conn):
    out = []
    rows = conn.execute("SELECT id FROM tasks WHERE status='waiting' "
                        "AND expire_at IS NOT NULL AND expire_at < ?",
                        (datetime.now().isoformat(timespec="seconds"),)).fetchall()
    for r in rows:
        models.move_task(conn, r["id"], "archived")
        out.append(r["id"])
    conn.commit()
    return out


def _claim(conn, task_id=None):
    """原子领取：定向（插队）或队首。成功返回任务 dict，否则 None。"""
    if task_id is not None:
        task = models.get_task(conn, task_id)
        if task is None or task["status"] != "waiting":
            return None
        if not models.try_start_task(conn, task_id):
            return None
        return models.get_task(conn, task_id)
    row = conn.execute("SELECT id FROM tasks WHERE status='waiting' AND "
                       "(expire_at IS NULL OR expire_at >= ?) "
                       "ORDER BY updated_at LIMIT 1",
                       (datetime.now().isoformat(timespec="seconds"),)).fetchone()
    if not row:
        return None
    if not models.try_start_task(conn, row["id"]):
        return None
    return models.get_task(conn, row["id"])


def _dispatch(db_path, base_path, task):
    """分发：复杂任务 spawn hermes agent；常规任务 spawn executor 子进程。"""
    log_dir = pathlib.Path(base_path) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logf = open(log_dir / f"executor-{task['id']}.log", "ab")
    if task.get("is_complex"):
        cmd = ["hermes", "chat", "-q",
               "$(cat " + str(pathlib.Path(base_path) / "prompts" / "complex-execute.txt") + ")",
               "--task-id", str(task["id"])]
    else:
        cmd = [sys.executable, "-m", "idea_hub.executor", "--db", db_path,
               "--task-id", str(task["id"]), "--base", base_path]
    subprocess.Popen(cmd, stdout=logf, stderr=logf, cwd=base_path)


def tick(db_path, base_path):
    """单次 tick。返回动作摘要 dict。"""
    result = {"claimed": [], "recovered": [], "expired": [],
              "skipped_budget": False}
    conn = db.connect(db_path)
    try:
        # 1. 健康心跳
        models.set_setting(conn, "last_scheduler_tick",
                           datetime.now().isoformat(timespec="seconds"))
        # 2. 预算
        max_tokens = int(models.get_setting(conn, "max_daily_tokens", "500000"))
        if models.daily_token_used(conn) >= max_tokens:
            result["skipped_budget"] = True
        # 3. auto_execute 门控（只影响步骤 6c 自动领取）
        auto_execute = models.get_setting(conn, "auto_execute", "1") == "1"
        # 4. 卡死回收（不受 auto_execute 影响）
        result["recovered"] = _recover_stale(conn)
        # 5. 过期归档（不受 auto_execute 影响）
        result["expired"] = _archive_expired(conn)
        if not result["skipped_budget"]:
            # 6. 领取
            max_concurrent = int(models.get_setting(conn, "max_concurrent", "1"))
            in_progress = conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='in_progress'").fetchone()[0]
            if in_progress < max_concurrent:
                # 6b 插队优先
                pending = conn.execute(
                    "SELECT task_id FROM execute_requests WHERE status='pending' "
                    "ORDER BY id").fetchall()
                claimed = None
                for p in pending:
                    claimed = _claim(conn, p["task_id"])
                    if claimed:
                        break
                # 6c 自动领取（auto_execute 开时）
                if claimed is None and auto_execute:
                    claimed = _claim(conn)
                if claimed:
                    result["claimed"].append(claimed["id"])
                    _dispatch(db_path, base_path, claimed)
        # 8. 通知清理（30 天）
        models.clear_old_notifications(conn, 30)
        conn.commit()
        return result
    finally:
        conn.close()


def main():
    import argparse
    p = argparse.ArgumentParser(prog="idea_hub.scheduler")
    p.add_argument("--db", default="data/idea.db")
    p.add_argument("--base", default=str(pathlib.Path.cwd()))
    args = p.parse_args()
    lock = _acquire_lock(str(pathlib.Path(args.base) / "logs" / "scheduler.lock"))
    if lock is None:
        print("skip: another tick is running")
        return
    try:
        r = tick(args.db, args.base)
        print(f"{datetime.now().isoformat(timespec='seconds')} "
              f"claimed={r['claimed']} recovered={r['recovered']} "
              f"expired={r['expired']} budget={r['skipped_budget']}")
    finally:
        lock.close()


if __name__ == "__main__":
    main()
```

`executor.py` 补 `if __name__ == "__main__"` 入口（argparse --db/--task-id/--base → execute_task）：

```python
if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(prog="idea_hub.executor")
    p.add_argument("--db", required=True)
    p.add_argument("--task-id", type=int, required=True)
    p.add_argument("--base", default=str(pathlib.Path.cwd()))
    a = p.parse_args()
    sys.exit(execute_task(a.db, a.task_id, a.base))
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_scheduler.py -v`
Expected: 6 passed（注意 `test_max_concurrent_blocks` 中 `_waiting` 后首个任务被置 in_progress，第二个 waiting 不被领取）

- [ ] **Step 5: 回归 + 提交**

Run: `.venv/Scripts/python.exe -m pytest -q`
Commit: `git add idea_hub/scheduler.py idea_hub/executor.py tests/test_scheduler.py && git commit -m "feat(scheduler): stateless tick - health beat, budget gate, stale recovery, expiry archive, concurrency limit, claim+dispatch"`

---

### Task 7: 生成侧（import-ideas 扩展 + sources ttl + generate prompt）

**Files:**
- Modify: `idea_hub/cli.py`（`cmd_import_ideas`）
- Modify: `idea_hub/models.py`（`create_source` 加 `ttl_hours` 参数；`create_task` 加 `content_type/expire_at` 透传）
- Modify: `scripts/deploy/prompts/generate.txt`
- Test: `tests/test_import_ideas_ext.py`（新建）

**Interfaces:**
- Consumes: Task 1（字段已存在）
- Produces: `import-ideas` 支持每条 JSON 的 `content_type` / `expire_at` 字段；`add-idea` 支持 `--content-type`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_import_ideas_ext.py
"""import-ideas 扩展：content_type / expire_at 落库。"""
import json
import subprocess
import sys
from pathlib import Path
from idea_hub import db, models

def _run(tmp_path: Path, file: Path):
    return subprocess.run([sys.executable, "-m", "idea_hub.cli", "--db",
                           str(tmp_path / "t.db"), "import-ideas", "--file", str(file)],
                          capture_output=True, text=True, cwd=str(tmp_path))

def test_import_content_type_and_expire(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    data = [{"title": "短内容", "summary": "s", "score": 9, "dims": "{}",
             "detail": "构思", "content_type": "short",
             "expire_at": "2026-08-20T00:00:00"}]
    f = tmp_path / "ideas.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = _run(tmp_path, f)
    assert r.returncode == 0
    tasks = models.list_tasks(conn, status="todo")
    assert len(tasks) == 1
    assert tasks[0]["content_type"] == "short"
    assert tasks[0]["expire_at"] == "2026-08-20T00:00:00"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_import_ideas_ext.py -v`
Expected: FAIL（content_type 未透传，默认 long）

- [ ] **Step 3: 实现**

（`models.create_task` / `create_source` 的签名扩展已在 Task 1 完成，此处直接使用。）

`cmd_import_ideas`：创建分支传 `content_type=item.get("content_type", "long")`、`expire_at=item.get("expire_at")`；relate 分支可更新 `expire_at`（若新热点时效更短）：

```python
                models.update_task(conn, task["id"], feasibility_score=item["score"],
                                   score_breakdown=item["dims"],
                                   idea_path=_write_draft(...),
                                   content_type=item.get("content_type") or task.get("content_type") or "long",
                                   expire_at=item.get("expire_at") or task.get("expire_at"))
```

`add-idea` 子命令加 `--content-type`（默认 long）与 `--expire-at`，透传 create_task。

`scripts/deploy/prompts/generate.txt`：在步骤 4 的 JSON 结构说明中增加：

```
每条元素结构（新增字段）：
  "content_type": "short|long|video_script"  # 必填。判定规则：
     热点事件/观点评论/时效性强 → short；深度话题/教程/知识梳理 → long；
     操作演示/流程向 → video_script
  "expire_at": "YYYY-MM-DDTHH:MM:SS" 或 null  # 时效截止（ISO），
     时效强的热点（百度热搜等）给较短有效期（如次日 0 点），深度话题给 null（不失效）
```

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_import_ideas_ext.py -v`
Expected: 1 passed

- [ ] **Step 5: 回归 + 提交**

Run: `.venv/Scripts/python.exe -m pytest -q`
Commit: `git add idea_hub/cli.py idea_hub/models.py scripts/deploy/prompts/generate.txt tests/test_import_ideas_ext.py && git commit -m "feat(gen): import-ideas content_type/expire_at, sources ttl_hours, generate prompt update"`

---

### Task 8: Web API 扩展（notifications / 调度健康 / 任务新字段 / 重置失败）

**Files:**
- Modify: `idea_hub/server.py`
- Test: `tests/test_api_execution.py`（新建）

**Interfaces:**
- Consumes: Task 1（models）、Task 2（notify）
- Produces 新端点：
  - `GET /api/notifications?unread_only=1` → `{"items": [...], "unread": n}`
  - `POST /api/notifications/{nid}/read` → 200
  - `POST /api/notifications/read-all` → 200
  - `POST /api/tasks/{task_id}/reset-failures` → 200（fail_count=0, last_fail_reason=NULL）
  - `GET /api/health` → `{"last_tick": "...", "minutes_ago": n|None, "today_tokens": n}`
  - `PATCH /api/tasks/{task_id}` 支持 `content_type/is_complex/redo_note` 字段

- [ ] **Step 1: 写失败测试**

```python
# tests/test_api_execution.py
"""API：通知读写、健康状态、任务新字段、重置失败计数。"""
from fastapi.testclient import TestClient
from idea_hub import db, models, server


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DB_PATH", str(tmp_path / "t.db"))
    conn = db.connect(str(tmp_path / "t.db"))
    return TestClient(server.app), conn

def test_notifications_api(tmp_path, monkeypatch):
    client, conn = _client(tmp_path, monkeypatch)
    models.create_notification(conn, task_id=None, type="done", title="t", body="b")
    r = client.get("/api/notifications")
    assert r.status_code == 200
    data = r.json()
    assert data["items"][0]["title"] == "t" and data["unread"] == 1
    nid = data["items"][0]["id"]
    client.post(f"/api/notifications/{nid}/read")
    assert client.get("/api/notifications").json()["unread"] == 0

def test_health_api(tmp_path, monkeypatch):
    client, conn = _client(tmp_path, monkeypatch)
    models.set_setting(conn, "last_scheduler_tick", "2026-08-13T00:00:00")
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert "last_tick" in d and "minutes_ago" in d and "today_tokens" in d

def test_patch_task_new_fields(tmp_path, monkeypatch):
    client, conn = _client(tmp_path, monkeypatch)
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    r = client.patch(f"/api/tasks/{tid}", json={"content_type": "short",
                                                "is_complex": 1,
                                                "redo_note": "口语化"})
    assert r.status_code == 200
    t = models.get_task(conn, tid)
    assert t["content_type"] == "short" and t["is_complex"] == 1
    assert t["redo_note"] == "口语化"

def test_reset_failures(tmp_path, monkeypatch):
    client, conn = _client(tmp_path, monkeypatch)
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=1,
                             feasibility_score=9, score_breakdown="{}")
    models.update_task(conn, tid, fail_count=3, last_fail_reason="x")
    r = client.post(f"/api/tasks/{tid}/reset-failures")
    assert r.status_code == 200
    t = models.get_task(conn, tid)
    assert t["fail_count"] == 0 and t["last_fail_reason"] is None
```

- [ ] **Step 2: 运行测试验证失败**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_execution.py -v`
Expected: FAIL（端点不存在）

- [ ] **Step 3: 实现 server.py**

在现有路由后追加：

```python
    @app.get("/api/notifications")
    def api_notifications(unread_only: int = 0):
        conn = db.connect(DB_PATH)
        try:
            items = models.list_notifications(conn, unread_only=bool(unread_only))
            unread = len(models.list_notifications(conn, unread_only=True))
            return {"items": items, "unread": unread}
        finally:
            conn.close()

    @app.post("/api/notifications/{nid}/read")
    def api_notification_read(nid: int):
        conn = db.connect(DB_PATH)
        try:
            models.mark_notification_read(conn, nid)
            return {"ok": True}
        finally:
            conn.close()

    @app.post("/api/notifications/read-all")
    def api_notifications_read_all():
        conn = db.connect(DB_PATH)
        try:
            models.mark_all_notifications_read(conn)
            return {"ok": True}
        finally:
            conn.close()

    @app.get("/api/health")
    def api_health():
        conn = db.connect(DB_PATH)
        try:
            health = models.get_health(conn)
            health["today_tokens"] = models.daily_token_used(conn)
            return health
        finally:
            conn.close()

    @app.post("/api/tasks/{task_id}/reset-failures")
    def api_reset_failures(task_id: int):
        conn = db.connect(DB_PATH)
        try:
            task = models.get_task(conn, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="task not found")
            models.update_task(conn, task_id, fail_count=0, last_fail_reason=None)
            return {"ok": True}
        finally:
            conn.close()
```

`PATCH /api/tasks/{task_id}` 的字段白名单追加：`"content_type", "is_complex", "redo_note"`（找到该端点内允许字段列表，加入）。

- [ ] **Step 4: 运行测试验证通过**

Run: `.venv/Scripts/python.exe -m pytest tests/test_api_execution.py -v`
Expected: 4 passed

- [ ] **Step 5: 回归 + 提交**

Run: `.venv/Scripts/python.exe -m pytest -q`
Commit: `git add idea_hub/server.py tests/test_api_execution.py && git commit -m "feat(api): notifications, health, task new fields, reset-failures endpoints"`

---

### Task 9: 前端（卡片徽标 / 详情面板 / 设置 / 顶部状态栏 / 通知中心）

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/style.css`

**Interfaces:**
- Consumes: Task 8 端点
- 行为：顶部栏显示调度器状态（minutes_ago 标红）、今日 token、未读角标；卡片显示类型徽标与复杂标记；详情面板可编辑 content_type/is_complex/redo_note、显示 fail_count/last_fail_reason、重置失败按钮、版本下载；设置弹窗含调度配置

- [ ] **Step 1: 实现顶部状态栏与未读角标（index.html + app.js + style.css）**

顶部栏在应用名右侧加状态区：

```html
<div class="topbar-status">
  <span id="sched-status" title="调度器状态">调度: --</span>
  <span id="token-today" title="今日 token 消耗">今日: --</span>
  <button id="notif-btn" class="notif-btn">通知 <span id="notif-badge" class="badge hidden">0</span></button>
</div>
```

`app.js` 轮询逻辑（合并进现有轮询/加载函数）：

```javascript
async function refreshHealth() {
  try {
    const h = await api('/api/health');
    const el = document.getElementById('sched-status');
    if (h.minutes_ago === null) {
      el.textContent = '调度: 未知';
      el.className = 'sched-warn';
    } else if (h.minutes_ago > 15) {
      el.textContent = `调度: ${h.minutes_ago} 分钟前`;
      el.className = 'sched-bad';
    } else {
      el.textContent = `调度: ${h.minutes_ago} 分钟前`;
      el.className = 'sched-ok';
    }
    document.getElementById('token-today').textContent = `今日: ${h.today_tokens}`;
  } catch (e) { /* 忽略，下次刷新重试 */ }
}
async function refreshNotifications() {
  try {
    const n = await api('/api/notifications?unread_only=1');
    const badge = document.getElementById('notif-badge');
    badge.textContent = n.unread;
    badge.classList.toggle('hidden', n.unread === 0);
  } catch (e) {}
}
// 每 60 秒刷新一次健康与未读（覆盖现有轮询间隔，如已有 30s 轮询则并入）
```

通知中心：点击 `#notif-btn` 打开抽屉（`<div id="notif-drawer">`），列出最近 50 条（title/body/type/created_at），"全部已读"按钮 → `POST /api/notifications/read-all`。

- [ ] **Step 2: 卡片徽标与详情面板**

卡片渲染（app.js 卡片模板）增加：

```javascript
const ctMap = { short: '短文', long: '长文', video_script: '视频' };
function badgeHtml(t) {
  let h = `<span class="ct-badge ct-${t.content_type || 'long'}">${ctMap[t.content_type] || '长文'}</span>`;
  if (t.is_complex) h += `<span class="ct-badge complex">复杂</span>`;
  return h;
}
```

详情面板增加字段（PATCH 保存，复用现有编辑保存逻辑）：
- content_type 下拉（short/long/video_script）
- 复杂任务 checkbox（提示判定标准：需联网调研/多源交叉验证/深度长文 >2000 字）
- redo_note 文本域（"打回意见"，保存进 redo_note）
- fail_count / last_fail_reason 展示 + "重置失败计数"按钮 → `POST /api/tasks/{id}/reset-failures`
- 版本列表：请求 `/api/tasks/{id}` 返回 output_path 后，前端可请求 `GET /outputs/tasks/{id}/output_v*.md`？——静态托管下直接构造 `<a href="/outputs/tasks/{id}/output.md">` 与 `output_vN.md`（FastAPI 已托管 outputs 静态目录则无需新端点；若未托管，在 server.py 加 `app.mount("/outputs", StaticFiles(...))`——检查现有挂载，无则 Task 8 补一行）

- [ ] **Step 3: 设置弹窗扩展**

现有设置弹窗（GET/PUT /api/settings）追加字段：auto_execute（开关）、max_concurrent（数字）、max_fail_count（数字）、stale_simple_min、stale_complex_min、max_daily_tokens、qq_target（文本，QQ 推送目标如 `qq:123456`，空=不推送）。
注意：PUT /api/settings 若为全量替换语义，前端保存时必须携带 settings 全量 dict（含 qq_target），避免覆盖丢失；实现时先确认现有前端保存逻辑。

- [ ] **Step 4: 人工走查**

浏览器打开 `http://127.0.0.1:8000`（本地起服务）：
- 顶部栏显示调度状态与今日 token、未读角标随通知变化
- 卡片显示类型徽标；详情面板编辑 content_type/复杂标记保存生效；失败信息与重置按钮可用
- 设置弹窗新配置保存后 `GET /api/settings` 回读正确

- [ ] **Step 5: 提交**

Commit: `git add web/index.html web/app.js web/style.css && git commit -m "feat(web): scheduler health bar, token usage, notification drawer, task badges, execution settings"`

---

### Task 10: 部署（crontab / complex-execute prompt / healthcheck / install 脚本）

**Files:**
- Create: `scripts/deploy/prompts/complex-execute.txt`
- Create: `scripts/deploy/healthcheck.sh`
- Modify: `scripts/deploy/crontab.txt`
- Modify: `scripts/deploy/install-server.sh`

- [ ] **Step 1: complex-execute.txt（复杂任务 agent prompt）**

```text
# Idea Hub 复杂任务执行 prompt（Hermes agent 定向执行单个任务）
# 由调度器 spawn：hermes chat -q "$(cat prompts/complex-execute.txt)" --task-id N
# 项目位于 $HOME/idea-hub，所有命令在项目根目录下执行。

你是 Idea Hub 的深度创作 Agent。执行单个复杂任务（定向 --task-id）。

执行步骤（严格按顺序）：
1. 运行 `uv run python -m idea_hub.cli --db data/idea.db next --task-id <N>` 确认任务已领取。
   失败（任务不在 waiting / 已领取）则输出说明并结束（不要 complete/fail）。
2. 读取任务的构思全文（任务 JSON 的 idea_path 指向文件），理解任务要求。
3. 深度执行创作（质量优先，允许多轮迭代与工具调用）：
   - 可联网调研（web_search/web_extract）补充真实素材
   - 阅读关联热点原文（如有 URL）
   - 产出完整内容，写入临时文件 /tmp/output.md（中文，结构完整）
4. 自审：对照以下维度自查，不满意则自行修改后再落盘：
   - 字数达标（按 content_type：短文 200-500 / 长文 1000-3000 / 视频脚本 250-750）
   - 无模板词（首先/其次/总的来说/值得注意的是等）
   - 具体数字/日期/引用有来源支撑；推测不写成事实
   - 结构完整（标题/分段/小标题或分镜）
5. 运行 `uv run python -m idea_hub.cli --db data/idea.db complete --task-id <N> --summary '<一句话摘要>' --output-path /tmp/output.md --token-used <估算值>`。
   token 估算从本次会话用量输出读取（如 15000）。
6. 发送完成通知：
   `hermes send --to qq:<群> "Idea Hub 任务完成\n《标题》 [长文]\n摘要：...\n产出：https://idea.helloyanze.top/"`（群 id 以实际配置为准，缺失则跳过）
7. 如遇不可恢复错误：运行 `uv run python -m idea_hub.cli --db data/idea.db fail --task-id <N> --reason '<原因>'` 退回等待队列，并用 hermes send 发送失败通知。

注意：
- 完成任务必须用 complete 命令（不要手动改数据库）
- 命令失败先看错误信息，判断是重试还是 fail
- 产出内容用中文
```

- [ ] **Step 2: healthcheck.sh（独立监控，调度器挂了也能告警）**

```bash
#!/usr/bin/env bash
# 调度器健康监控：每 15 分钟 cron 运行。last_scheduler_tick 超 15 分钟未更新则 QQ 告警。
set -euo pipefail
cd "$HOME/idea-hub"
LAST=$(.venv/bin/python -c "
from idea_hub import db
c = db.connect('data/idea.db')
print(c.execute(\"SELECT value FROM settings WHERE key='last_scheduler_tick'\").fetchone()[0])
c.close()")
if [ -z "$LAST" ]; then exit 0; fi
MIN=$(.venv/bin/python -c "
from datetime import datetime
from idea_hub import db
c = db.connect('data/idea.db')
ts = c.execute(\"SELECT value FROM settings WHERE key='last_scheduler_tick'\").fetchone()[0]
c.close()
try:
    last = datetime.fromisoformat(ts)
    print(int((datetime.now() - last).total_seconds() // 60))
except Exception:
    print('999')")
if [ "${MIN:-999}" -gt 15 ]; then
  set -a; source .env 2>/dev/null || true; set +a
  hermes send --to "${QQ_TARGET:-qq:$(grep -oP 'qq:\K[0-9]+' .env 2>/dev/null || echo 0)}" \
    "Idea Hub 调度器异常：最后运行于 ${LAST}（${MIN} 分钟前），请检查 crontab 与日志" \
    || echo "healthcheck send failed" >> logs/healthcheck.log
fi
```

- [ ] **Step 3: crontab.txt 更新**

替换 03:00 执行条目为：

```
# 调度器：每 5 分钟检查等待队列并分发执行（auto_execute=0 时跳过自动领取）
*/5 * * * * cd $HOME/idea-hub && export https_proxy=http://127.0.0.1:7890 http_proxy=http://127.0.0.1:7890 && $HOME/.local/bin/uv run python -m idea_hub.scheduler --db data/idea.db >> logs/scheduler.log 2>&1

# 调度器健康监控：每 15 分钟检查 last_scheduler_tick
*/15 * * * * cd $HOME/idea-hub && bash scripts/deploy/healthcheck.sh >> logs/healthcheck.log 2>&1
```

保留 02:00 收集+生成（generate.txt 已更新）、03:30 备份；删除原 `0 3 * * * ... execute.txt` 行。

- [ ] **Step 4: install-server.sh 追加**

在部署脚本的同步段（复制 web/、idea_hub/ 等之后）追加复制 `scripts/deploy/healthcheck.sh` 并 `chmod +x`；确保云端 `prompts/` 目录含 `complex-execute.txt` 与更新后的 `generate.txt`。

- [ ] **Step 5: 提交**

Commit: `git add scripts/deploy/prompts/complex-execute.txt scripts/deploy/healthcheck.sh scripts/deploy/crontab.txt scripts/deploy/install-server.sh && git commit -m "feat(deploy): complex-execute agent prompt, healthcheck cron, scheduler crontab"`

---

### Task 11: 端到端集成测试 + 回归核对

**Files:**
- Create: `tests/test_e2e_auto_execution.py`

- [ ] **Step 1: 写端到端测试**

```python
# tests/test_e2e_auto_execution.py
"""端到端：waiting → 调度（mock API）→ 质检通过 → done → 通知记录。"""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
from idea_hub import db, executor, models, scheduler

def test_full_chain(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    tid = models.create_task(conn, title="端到端任务", idea_summary="s",
                             target_id=1, feasibility_score=9, score_breakdown="{}",
                             content_type="short")
    models.move_task(conn, tid, "waiting")
    conn.commit()

    payload = json.dumps({"title": "标题", "content": "短文内容" * 60, "word_count": 240})
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload, 500), (qa_ok, 200)]
    with patch("idea_hub.executor.call_llm", side_effect=lambda p, **k: seq.pop(0)), \
         patch("subprocess.Popen") as pop, \
         patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert r["claimed"] == [tid]
    # tick 分发后，模拟子进程执行（executor.execute_task）
    with patch("idea_hub.executor.call_llm", side_effect=[]):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    task = models.get_task(conn, tid)
    assert task["status"] == "done"
    out = Path(tmp_path) / "outputs" / "tasks" / str(tid) / "output.md"
    assert out.exists() and out.stat().st_size > 0
    assert task["token_used"] >= 700
    # 通知记录（调度完成后由执行器/agent 发，此处验证表可查）
    models.create_notification(conn, task_id=tid, type="done", title="完成", body="摘要")
    assert len(models.list_notifications(conn)) == 1
```

注意：端到端中 `executor.execute_task` 第二次调用时 `call_llm` 被 mock 为空列表会 IndexError——上面用 `side_effect=[]` 触发 IndexError 会导致 fail 分支。修正为提供足够序列：第二次执行前 output.md 已由第一次 tick 分发不存在（tick 只分发不执行），故 execute_task 会走完整生成流程，需再提供 payload/qa。上面代码已在第二次 patch 提供空列表——改为复用第一次序列即可（或直接允许调用真实 mock 序列继续）。

```python
def test_full_chain(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    tid = models.create_task(conn, title="端到端任务", idea_summary="s",
                             target_id=1, feasibility_score=9, score_breakdown="{}",
                             content_type="short")
    models.move_task(conn, tid, "waiting")
    conn.commit()
    payload = json.dumps({"title": "标题", "content": "短文内容" * 60, "word_count": 240})
    qa_ok = json.dumps({"pass": True, "issues": [], "suggestions": ""})
    seq = [(payload, 500), (qa_ok, 200)]
    with patch("idea_hub.executor.call_llm", side_effect=lambda p, **k: seq.pop(0)), \
         patch("subprocess.Popen"), \
         patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert r["claimed"] == [tid]
    # 模拟子进程执行
    seq2 = [(payload, 500), (qa_ok, 200)]
    with patch("idea_hub.executor.call_llm", side_effect=lambda p, **k: seq2.pop(0)):
        rc = executor.execute_task(str(tmp_path / "t.db"), tid, str(tmp_path))
    assert rc == 0
    task = models.get_task(conn, tid)
    assert task["status"] == "done"
    assert (Path(tmp_path) / "outputs" / "tasks" / str(tid) / "output.md").exists()
    assert task["token_used"] >= 700
```

- [ ] **Step 2: 运行测试**

Run: `.venv/Scripts/python.exe -m pytest tests/test_e2e_auto_execution.py -v`
Expected: 1 passed

- [ ] **Step 3: 全量回归**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: 原 78 + 新增（迁移 5 + 通知 3 + 模板 7 + executor 7 + cli 3 + scheduler 6 + import 1 + api 4 + e2e 1 = 37）共 115 passed

- [ ] **Step 4: 核对规格回归清单**

对照规格第 6 章逐项走查（代码层面）：
- 6.1 功能闭环：调度领取/分层执行/类型产出/通知双写/插队/打回版本 —— 对应 Task 4/6/8/9/10
- 6.2 稳定性：失败重试与暂停/心跳回收/幂等/过期/预算/并发/文件锁 —— 对应测试用例
- 6.3 数据兼容：迁移字段/回填/旧 cron 移除 —— Task 1/10

- [ ] **Step 5: 提交**

Commit: `git add tests/test_e2e_auto_execution.py && git commit -m "test(e2e): auto-execution full chain (claim -> execute -> done -> notify)"`

---

## Self-Review Notes

- **规格覆盖核对**：v4 规格 5.1-5.11 全部有对应任务（架构=Task 6，执行器=Task 4，模板=Task 3，通知=Task 2/8/9，生成侧=Task 7，部署=Task 10，测试=Task 11）；6.1-6.3 回归清单在 Task 11 Step 4 核对。
- **接口一致性**：`models.create_task` 扩展参数（Task 7）不破坏 Task 1/4/5 中调用（关键字参数带默认值）；`executor.execute_task` 签名在 Task 4 定义、Task 5 CLI 与 Task 11 e2e 使用一致；`scheduler.tick` 返回 dict 在 Task 6 定义、Task 11 消费一致。
- **已知待办（实现时确认）**：server.py 是否已 mount `/outputs` 静态目录（Task 9 Step 2 提到，若未挂载需在 Task 8 补一行 `app.mount("/outputs", StaticFiles(directory=...))`）；`executor.py` 中 `models.connect` 的延迟导入写法在实现时统一为 `from idea_hub import db`。
