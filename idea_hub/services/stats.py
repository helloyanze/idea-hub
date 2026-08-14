"""Persistence helpers for dashboard statistics."""

from datetime import date, timedelta


def get_stats(conn) -> dict:
    queue = {status: 0 for status in ("todo", "waiting", "in_progress", "done")}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
    ).fetchall():
        queue[row["status"]] = row["count"]

    hotspot_counts = {"total": 0, "admit": 0, "discard": 0}
    for row in conn.execute(
        "SELECT verdict, COUNT(*) AS count FROM hot_items GROUP BY verdict"
    ).fetchall():
        hotspot_counts[row["verdict"]] = row["count"]
        hotspot_counts["total"] += row["count"]

    execution_total = conn.execute(
        "SELECT COALESCE(SUM(token_used), 0) FROM tasks"
    ).fetchone()[0]
    generation_total = conn.execute(
        "SELECT COALESCE(SUM(token_used), 0) FROM jobs "
        "WHERE type IN ('collect', 'generate')"
    ).fetchone()[0]
    today_produced = conn.execute(
        "SELECT COUNT(*) FROM tasks "
        "WHERE status = 'done' AND date(completed_at) = date('now')"
    ).fetchone()[0]
    active_jobs = conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'running')"
    ).fetchone()[0]
    scheduler_row = conn.execute(
        "SELECT value FROM settings WHERE key = 'scheduler_last_tick'"
    ).fetchone()

    return {
        "queue": queue,
        "hotspots": hotspot_counts,
        "tokens": {
            "execution_total": execution_total,
            "generation_total": generation_total,
        },
        "today_produced": today_produced,
        "active_jobs": active_jobs,
        "scheduler": {
            "last_tick": scheduler_row["value"] if scheduler_row is not None else None
        },
    }


def get_trends(conn, days: int = 7) -> dict:
    days = max(1, min(days, 90))
    today_value = conn.execute("SELECT date('now')").fetchone()[0]
    start_value = conn.execute(
        "SELECT date('now', ?)",
        (f"-{days - 1} days",),
    ).fetchone()[0]

    def grouped_counts(sql: str) -> dict[str, int]:
        return {
            row["date"]: row["count"]
            for row in conn.execute(sql, (start_value,)).fetchall()
        }

    hotspot_counts = grouped_counts(
        "SELECT collected_date AS date, COUNT(*) AS count FROM hot_items "
        "WHERE collected_date >= ? GROUP BY collected_date"
    )
    task_counts = grouped_counts(
        "SELECT date(created_at) AS date, COUNT(*) AS count FROM tasks "
        "WHERE date(created_at) >= ? GROUP BY date(created_at)"
    )
    output_counts = grouped_counts(
        "SELECT date(created_at) AS date, COUNT(*) AS count FROM outputs "
        "WHERE date(created_at) >= ? GROUP BY date(created_at)"
    )

    start_date = date.fromisoformat(start_value)
    today = date.fromisoformat(today_value)
    items = []
    current = start_date
    while current <= today:
        day = current.isoformat()
        items.append(
            {
                "date": day,
                "hotspots": hotspot_counts.get(day, 0),
                "tasks": task_counts.get(day, 0),
                "outputs": output_counts.get(day, 0),
            }
        )
        current += timedelta(days=1)
    return {"items": items}
