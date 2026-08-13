"""调度器 tick：预算/回收/过期/并发/领取/插队/分发。"""
import sqlite3
from pathlib import Path
from unittest.mock import patch
from idea_hub import db, models, scheduler

def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)  # connect 不自动建表，按仓库惯例显式初始化
    # target_id=1 外键依赖 targets 表，需先建行（首个插入 id=1）
    conn.execute("INSERT INTO targets (name, description, score_dimensions, is_active) VALUES (?,?,?,1)",
                 ("测试目标", "测试", "{}"))
    conn.commit()
    return conn

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
    conn = _db(tmp_path)
    tid = _waiting(tmp_path, conn)[0]
    with patch("subprocess.Popen") as pop, \
         patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert r["claimed"] == [tid]
    assert models.get_task(conn, tid)["status"] == "in_progress"
    pop.assert_called_once()

def test_budget_skip(tmp_path):
    conn = _db(tmp_path)
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
    conn = _db(tmp_path)
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
    conn = _db(tmp_path)
    tid = _waiting(tmp_path, conn)[0]
    conn.execute("UPDATE tasks SET expire_at='2000-01-01T00:00:00' WHERE id=?", (tid,))
    conn.commit()
    with patch("idea_hub.scheduler._acquire_lock", return_value=object()):
        r = scheduler.tick(str(tmp_path / "t.db"), str(tmp_path))
    assert tid in r["expired"]
    assert models.get_task(conn, tid)["status"] == "archived"

def test_max_concurrent_blocks(tmp_path):
    conn = _db(tmp_path)
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
    l1.close()
    l3 = scheduler._acquire_lock(str(tmp_path / "sched.lock"))
    assert l3 is not None
    l3.close()
