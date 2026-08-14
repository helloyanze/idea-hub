"""Job lifecycle and collect-job execution services."""

import json

from .. import db, models
from ..collectors import collect_all
from ..errors import AppError, BAD_REQUEST
from ..services.filtering import apply_keywords_filter, dedup_by_url, truncate_snapshot
from ..services import generate
from ..services import settings as settings_service
from ..services.notify import emit
from ..services.scorer import score_items
from ..services.tasks import create_from_generation


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
    # Formal stale-job reclamation is handled by the S5.5 scheduler.
    row = conn.execute(
        "SELECT id FROM jobs WHERE type=? AND status='running' AND ("
        "(heartbeat_at IS NOT NULL "
        "AND heartbeat_at >= datetime('now', '-5 minutes')) OR "
        "(heartbeat_at IS NULL "
        "AND created_at >= datetime('now', '-5 minutes'))"
        ") ORDER BY id LIMIT 1",
        (type,),
    ).fetchone()
    return row[0] if row is not None else None


def run_collect_job(job_id, payload, db_path, api_key=None) -> None:
    """Execute a collect job synchronously; callers may run it in a worker thread."""
    conn = None
    try:
        conn = db.connect(db_path)
        mark_running(job_id)
        heartbeat(job_id)
        settings_map = settings_service.get_all(conn)
        dimensions = settings_map.get("score_dimensions") or [
            "facts", "verification", "timeliness", "value"
        ]
        threshold = settings_map.get("score_todo_threshold") or 8
        source_ids = (payload or {}).get("source_ids")
        rows = models.list_sources(conn, enabled_only=source_ids is None)
        if source_ids is not None:
            id_set = set(source_ids)
            rows = [row for row in rows if row["id"] in id_set]

        total = len(rows)
        inserted_total = 0
        admit_total = 0
        discard_total = 0
        token_total = 0
        errors = []
        for index, row in enumerate(rows, start=1):
            try:
                result = collect_all(conn, source_ids=[row["id"]])
                errors.extend(result["errors"])
                items = apply_keywords_filter(result["items"], row["keywords"])
                items = dedup_by_url(conn, items)
                if items:
                    usage = {}
                    scored = score_items(
                        items,
                        api_key=api_key,
                        dimensions=dimensions,
                        threshold=threshold,
                        token_usage=usage,
                    )
                    token_total += usage.get("total", 0)
                else:
                    scored = []
                for item in scored:
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO hot_items "
                        "(source_id, title, url, content_snapshot, final_score, score_breakdown, verdict) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            item.source_id,
                            item.title,
                            item.url,
                            truncate_snapshot(item.content_snapshot),
                            item.final_score if item.final_score is not None else 0,
                            json.dumps(item.score_breakdown, ensure_ascii=False),
                            item.verdict,
                        ),
                    )
                    if cursor.rowcount:
                        inserted_total += 1
                        if item.verdict == "admit":
                            admit_total += 1
                        else:
                            discard_total += 1
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
        if finish(job_id, "done", result_ref=result_ref, token_used=token_total):
            if api_key:
                body = (
                    f"新增 {inserted_total} 条热点，来源 {total} 个"
                    f"（收录 {admit_total} 条 / 丢弃 {discard_total} 条）"
                )
            else:
                body = f"降级全收：新增 {inserted_total} 条热点，来源 {total} 个"
            emit(
                conn,
                "collect_done",
                "热点收集完成",
                body,
                "info",
                entity_type="job",
                entity_id=job_id,
            )
    except Exception as exc:
        finish(job_id, "failed", error=str(exc))
        if conn is not None:
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


def run_generate_job(job_id, payload, db_path, api_key, base_path=None) -> None:
    """Execute a generate job synchronously; callers may run it in a worker thread."""
    if base_path is None:
        from ..config import load as load_config

        base_path = load_config().base_path
    conn = None
    try:
        conn = db.connect(db_path)
        mark_running(job_id)
        heartbeat(job_id)
        body = payload or {}
        candidates = generate.get_candidates(
            conn, count=body.get("count"), hotspot_ids=body.get("hotspot_ids")
        )
        total = len(candidates)
        task_ids = []
        failed_items = []
        token_total = 0
        if candidates:
            token_usage = {}
            gens = generate.generate_one(
                candidates,
                api_key,
                heartbeat=lambda: heartbeat(job_id),
                token_usage=token_usage,
            )
            token_total = int(token_usage.get("total") or 0)
        else:
            gens = []
        dropped = max(0, total - len(gens))
        for index, (candidate, gen) in enumerate(zip(candidates, gens), start=1):
            try:
                task_id = create_from_generation(conn, gen, candidate, base_path=base_path)
                task_ids.append(task_id)
            except Exception as exc:
                failed_items.append(
                    {"hotspot_id": candidate["hotspot_id"], "error": str(exc)}
                )
            finally:
                update_progress(job_id, int(index / total * 100) if total else 100)
                heartbeat(job_id)
        update_progress(job_id, 100)
        result = {
            "task_ids": task_ids,
            "task_count": len(task_ids),
            "dropped": dropped,
        }
        if failed_items:
            result["failed_items"] = failed_items
        result_ref = json.dumps(result, ensure_ascii=False)
        if finish(job_id, "done", result_ref=result_ref, token_used=token_total):
            if failed_items or dropped:
                notification_body = f"生成 {len(task_ids)} 个构思"
                if failed_items:
                    notification_body += f"，失败 {len(failed_items)} 个"
                if dropped:
                    notification_body += f"，缺 {dropped} 个"
                emit(
                    conn,
                    "generate_done",
                    "构思生成完成（部分失败）",
                    notification_body,
                    "warn",
                    entity_type="job",
                    entity_id=job_id,
                )
            else:
                emit(
                    conn,
                    "generate_done",
                    "构思生成完成",
                    f"生成 {len(task_ids)} 个构思",
                    "info",
                    entity_type="job",
                    entity_id=job_id,
                )
    except Exception as exc:
        finish(job_id, "failed", error=str(exc))
        if conn is not None:
            emit(
                conn,
                "job_failed",
                "生成任务失败",
                str(exc),
                "error",
                entity_type="job",
                entity_id=job_id,
            )
    finally:
        if conn is not None:
            conn.close()
