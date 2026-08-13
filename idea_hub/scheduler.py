"""无状态调度器（cron 每 5 分钟触发，tick 只分发不等待）。

tick 顺序：健康心跳 → 预算检查 → auto_execute 门控（只影响自动领取）
→ 卡死回收 → 过期归档 → 并发检查 → 领取（插队优先）→ 分发（spawn 子进程）→ 通知清理。
"""
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

from idea_hub import db, models, notify


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
            # updated_at 是 SQLite datetime('now') 输出的 UTC 空格格式
            # （'YYYY-MM-DD HH:MM:SS'），先替换为 ISO 'T' 再解析，避免 ValueError；
            # 同时与 UTC 当前时间比较（datetime.now() 本地时间会误判时区差）
            updated = datetime.fromisoformat(r["updated_at"].replace(" ", "T"))
            stale = (datetime.now(timezone.utc).replace(tzinfo=None) - updated).total_seconds() > limit * 60
        except (TypeError, ValueError):
            stale = True  # 时间解析失败按卡死处理
        if stale:
            models.update_task(conn, r["id"],
                               fail_count=(models.get_task(conn, r["id"])["fail_count"] or 0) + 1,
                               last_fail_reason="执行超时回收")
            models.move_task(conn, r["id"], "waiting")
            out.append(r["id"])
    conn.commit()
    return out


def _archive_expired(conn):
    out = []
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    rows = conn.execute("SELECT id, title FROM tasks WHERE status='waiting' "
                        "AND expire_at IS NOT NULL AND expire_at < ?",
                        (now_utc,)).fetchall()
    for r in rows:
        models.move_task(conn, r["id"], "archived")
        out.append(r["id"])
        notify.send(conn, task_id=r["id"], type="expired", title="Idea Hub 任务已归档",
                    body=f"《{r['title']}》热点时效已过")
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
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
    # 失败暂停：连续失败达 max_fail_count 的任务不再被自动领取（插队分支不检查，用户显式意图）
    max_fail = int(models.get_setting(conn, "max_fail_count", "3"))
    row = conn.execute("SELECT id FROM tasks WHERE status='waiting' AND "
                       "(expire_at IS NULL OR expire_at >= ?) AND "
                       "(fail_count IS NULL OR fail_count < ?) "
                       "ORDER BY updated_at LIMIT 1",
                       (now_utc, max_fail)).fetchone()
    if not row:
        return None
    if not models.try_start_task(conn, row["id"]):
        return None
    return models.get_task(conn, row["id"])


def _dispatch(db_path, base_path, task):
    """分发：复杂任务 spawn hermes agent；常规任务 spawn executor 子进程。"""
    log_dir = pathlib.Path(base_path) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    if task.get("is_complex"):
        # 直接读取 prompt 文件内容作为单参数传入（Popen 非 shell 模式，
        # "$(cat ...)" 字符串不会被展开，需在 Python 侧读文件）
        prompt_path = pathlib.Path(base_path) / "prompts" / "complex-execute.txt"
        if not prompt_path.exists():
            # 复杂 prompt 文件由 Task 10 创建，当前缺失时跳过分发，
            # 任务保持 in_progress 由超时回收兜底
            print(f"{datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec='seconds')} "
                  f"warn: prompts/complex-execute.txt missing, skip spawn for task {task['id']}")
            return
        prompt = prompt_path.read_text(encoding="utf-8")
        # 模板占位符 <N> 全部替换为任务 id（hermes chat 无 --task-id 参数，
        # 任务上下文只能通过 prompt 文本注入）
        prompt_text = prompt.replace("<N>", str(task["id"]))
        cmd = ["hermes", "chat", "-q", prompt_text]
    else:
        cmd = [sys.executable, "-m", "idea_hub.executor", "--db", db_path,
               "--task-id", str(task["id"]), "--base", base_path]
    logf = open(log_dir / f"executor-{task['id']}.log", "ab")
    subprocess.Popen(cmd, stdout=logf, stderr=logf, cwd=base_path)
    logf.close()


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
            # 预算通知幂等：同日只记 1 条（settings last_budget_notified 存日期，跨日重置）
            today = datetime.now().strftime("%Y-%m-%d")
            if models.get_setting(conn, "last_budget_notified", "") != today:
                notify.send(conn, task_id=None, type="budget",
                            title="Idea Hub 预算已用尽",
                            body="今日 token 预算已用尽，自动执行已暂停（次日恢复；立即执行仍可用）")
                models.set_setting(conn, "last_budget_notified", today)
        # 3. auto_execute 门控（只影响步骤 6c 自动领取）
        auto_execute = models.get_setting(conn, "auto_execute", "1") == "1"
        # 4. 卡死回收（不受 auto_execute 影响）
        result["recovered"] = _recover_stale(conn)
        # 5. 过期归档（不受 auto_execute 影响）
        result["expired"] = _archive_expired(conn)
        # 6. 领取（并发上限内；插队不受预算门控，自动领取受预算+auto_execute 双重门控）
        max_concurrent = int(models.get_setting(conn, "max_concurrent", "1"))
        in_progress = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='in_progress'").fetchone()[0]
        if in_progress < max_concurrent:
            # 6b 插队优先：用户明确要跑的必须能跑，预算超限也放行
            pending = conn.execute(
                "SELECT task_id FROM execute_requests WHERE status='pending' "
                "ORDER BY id").fetchall()
            claimed = None
            for p in pending:
                claimed = _claim(conn, p["task_id"])
                if claimed:
                    break
            # 6c 自动领取（auto_execute 开且预算未超限时）
            if claimed is None and auto_execute and not result["skipped_budget"]:
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
    logs_dir = pathlib.Path(args.base) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    lock = _acquire_lock(str(logs_dir / "scheduler.lock"))
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
