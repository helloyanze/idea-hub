import requests
from datetime import datetime, timedelta, timezone

from .services import jobs as jobs_service
from .services import settings as settings_service
from .services.notify import emit


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _read_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row is not None else default


def _write_setting(conn, key, value):
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value, value_type) "
        "VALUES (?, ?, 'string')",
        (key, value),
    )


def tick(conn, config) -> dict:
    _write_setting(conn, "scheduler_last_tick", _now_str())
    expired_count = _expire_tasks(conn)
    cleaned_count = _clean_discards(conn)
    recovered_count = _recover_stale_jobs(conn)
    collect_triggered = _maybe_trigger_collect(conn, config)
    conn.commit()
    return {
        "expired_count": expired_count,
        "cleaned_count": cleaned_count,
        "recovered_count": recovered_count,
        "collect_triggered": collect_triggered,
    }


def _expire_tasks(conn) -> int:
    rows = conn.execute(
        "SELECT id, title FROM tasks "
        "WHERE status IN ('todo','waiting') "
        "AND expire_at IS NOT NULL "
        "AND expire_at < datetime('now')"
    ).fetchall()
    cursor = conn.execute(
        "UPDATE tasks SET status='done', completed_at=datetime('now'), "
        "updated_at=datetime('now'), notes = CASE WHEN notes = '' "
        "THEN '已过期自动完成' ELSE notes || char(10) || '已过期自动完成' END "
        "WHERE status IN ('todo','waiting') "
        "AND expire_at IS NOT NULL "
        "AND expire_at < datetime('now')"
    )
    for row in rows:
        emit(
            conn,
            "task_expired",
            "任务已过期",
            f"《{row['title']}》已过期自动完成",
            "info",
            entity_type="task",
            entity_id=row["id"],
        )
    return cursor.rowcount


def _clean_discards(conn) -> int:
    retention_days = int(
        settings_service.get_all(conn).get("discard_retention_days") or 7
    )
    cursor = conn.execute(
        "DELETE FROM hot_items "
        "WHERE verdict='discard' AND collected_date <= date('now', ?)",
        (f"-{retention_days} days",),
    )
    count = cursor.rowcount
    if count > 0:
        emit(
            conn,
            "discard_cleaned",
            "已清理过期丢弃内容",
            f"清理 {count} 条过期 discard 热点",
            "info",
        )
    return count


def _recover_stale_jobs(conn) -> int:
    rows = conn.execute(
        "SELECT id, type FROM jobs WHERE status='running' AND ("
        "(heartbeat_at IS NOT NULL "
        "AND heartbeat_at < datetime('now', '-5 minutes')) OR "
        "(heartbeat_at IS NULL "
        "AND created_at < datetime('now', '-5 minutes')))"
    ).fetchall()
    cursor = conn.execute(
        "UPDATE jobs SET status='failed', error='stale: heartbeat timeout', "
        "updated_at=datetime('now') WHERE status='running' AND ("
        "(heartbeat_at IS NOT NULL "
        "AND heartbeat_at < datetime('now', '-5 minutes')) OR "
        "(heartbeat_at IS NULL "
        "AND created_at < datetime('now', '-5 minutes')))"
    )
    for row in rows:
        emit(
            conn,
            "job_failed",
            "任务已回收（心跳超时）",
            "stale: heartbeat timeout",
            "error",
            entity_type="job",
            entity_id=row["id"],
        )
    return cursor.rowcount


def _maybe_trigger_collect(conn, config) -> bool:
    interval_hours = int(
        settings_service.get_all(conn).get("collect_interval_hours") or 24
    )
    last_raw = _read_setting(conn, "scheduler_last_collect")
    if last_raw is None:
        due = True
    else:
        try:
            last = datetime.strptime(last_raw, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            due = datetime.now(timezone.utc) - last >= timedelta(hours=interval_hours)
        except ValueError:
            due = True
    if not due:
        return False
    if jobs_service.dedup_running(conn, "collect") is not None:
        return False

    host = config.host if config.host and config.host != "0.0.0.0" else "127.0.0.1"
    url = f"http://{host}:{config.port}/api/v1/collect"
    auth = None
    if config.auth_user and config.auth_pass:
        auth = (config.auth_user, config.auth_pass)
    try:
        response = requests.post(url, auth=auth, timeout=5)
        success = response.ok
    except Exception:
        success = False
    if success:
        _write_setting(conn, "scheduler_last_collect", _now_str())
        return True
    return False
