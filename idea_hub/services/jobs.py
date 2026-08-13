"""Job lifecycle and collect-job execution services."""

import json

from .. import db, models
from ..collectors import collect_all
from ..errors import AppError, BAD_REQUEST
from ..services.notify import emit
from ..services.filtering import apply_keywords_filter, dedup_by_url, truncate_snapshot


_db_path: str | None = None


def create_job(conn, type, payload) -> int:
    """Create a pending job and register the database path for lifecycle updates."""
    if type not in ("collect", "generate", "execute"):
        raise AppError(
            status_code=400,
            code=BAD_REQUEST,
            message=f"Invalid job type: {type}",
        )
    if not isinstance(payload, dict):
        raise AppError(
            status_code=400,
            code=BAD_REQUEST,
            message="payload must be a dict",
        )

    global _db_path
    rows = conn.execute("PRAGMA database_list").fetchall()
    for row in rows:
        if row[0] == 0:
            _db_path = row[2]
            break
    cursor = conn.execute("INSERT INTO jobs (type) VALUES (?)", (type,))
    conn.commit()
    return cursor.lastrowid


def _open_registered_db():
    if _db_path is None:
        raise RuntimeError("jobs: database path not registered")
    return db.connect(_db_path)


def mark_running(job_id) -> int:
    conn = _open_registered_db()
    try:
        cursor = conn.execute(
            "UPDATE jobs SET status='running', heartbeat_at=datetime('now'), "
            "updated_at=datetime('now') WHERE id=? AND status='pending'",
            (job_id,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def heartbeat(job_id) -> int:
    conn = _open_registered_db()
    try:
        cursor = conn.execute(
            "UPDATE jobs SET heartbeat_at=datetime('now'), updated_at=datetime('now') "
            "WHERE id=? AND status='running'",
            (job_id,),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def update_progress(job_id, pct) -> int:
    pct = max(0, min(100, int(pct)))
    conn = _open_registered_db()
    try:
        cursor = conn.execute(
            "UPDATE jobs SET progress=?, updated_at=datetime('now') "
            "WHERE id=? AND status='running'",
            (pct, job_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def finish(job_id, status, result_ref=None, error=None, token_used=None) -> int:
    if status not in ("done", "failed"):
        raise AppError(
            status_code=400,
            code=BAD_REQUEST,
            message=f"Invalid job status: {status}",
        )
    conn = _open_registered_db()
    try:
        cursor = conn.execute(
            "UPDATE jobs SET status=?, result_ref=?, error=?, "
            "token_used=COALESCE(?, token_used), updated_at=datetime('now') "
            "WHERE id=? AND status='running'",
            (status, result_ref, error, token_used, job_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def dedup_running(conn, type) -> int | None:
    row = conn.execute(
        "SELECT id FROM jobs WHERE type=? AND status='running' ORDER BY id LIMIT 1",
        (type,),
    ).fetchone()
    return row[0] if row is not None else None


def run_collect_job(job_id, payload, db_path) -> None:
    """Execute a collect job synchronously; callers may run it in a worker thread."""
    conn = None
    try:
        conn = db.connect(db_path)
        mark_running(job_id)
        heartbeat(job_id)
        source_ids = (payload or {}).get("source_ids")
        rows = models.list_sources(conn, enabled_only=source_ids is None)
        if source_ids is not None:
            id_set = set(source_ids)
            rows = [row for row in rows if row["id"] in id_set]

        total = len(rows)
        inserted_total = 0
        errors = []
        for index, row in enumerate(rows, start=1):
            try:
                result = collect_all(conn, source_ids=[row["id"]])
                errors.extend(result["errors"])
                items = apply_keywords_filter(result["items"], row["keywords"])
                items = dedup_by_url(conn, items)
                for item in items:
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO hot_items "
                        "(source_id, title, url, content_snapshot, verdict) "
                        "VALUES (?, ?, ?, ?, 'admit')",
                        (
                            item.source_id,
                            item.title,
                            item.url,
                            truncate_snapshot(item.content_snapshot),
                        ),
                    )
                    inserted_total += cursor.rowcount
                conn.commit()
            except Exception as exc:
                errors.append({"source_id": row["id"], "error": str(exc)})
            finally:
                update_progress(job_id, int(index / total * 100) if total else 100)
                heartbeat(job_id)

        if total == 0:
            update_progress(job_id, 100)
        result_ref = json.dumps(
            {"hotspot_count": inserted_total, "errors": errors},
            ensure_ascii=False,
        )
        if finish(job_id, "done", result_ref=result_ref):
            emit(
                conn,
                "collect_done",
                "热点收集完成",
                f"新增 {inserted_total} 条热点，来源 {total} 个",
                "info",
                entity_type="job",
                entity_id=job_id,
            )
    except Exception as exc:
        finish(job_id, "failed", error=str(exc))
        emit(
            conn,
            "job_failed",
            "收集任务失败",
            str(exc),
            "error",
            entity_type="job",
            entity_id=job_id,
        )
    finally:
        if conn is not None:
            conn.close()
