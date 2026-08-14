"""生成构思对应的任务创建服务。"""

import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from ..errors import AppError, BAD_REQUEST, NOT_FOUND
from .tags import upsert_by_names


def create_from_generation(conn, gen: dict, candidate: dict, base_path: str) -> int:
    """由生成结果和候选热点创建待办任务。"""
    feasibility = (
        int(round(candidate["final_score"]))
        if candidate["final_score"] is not None
        else 0
    )
    score_breakdown_json = json.dumps(
        candidate["score_breakdown"] or {}, ensure_ascii=False
    )
    if candidate["ttl_hours"] is None or not candidate["collected_at"]:
        expire_at = None
    else:
        expire_at = (
            datetime.strptime(candidate["collected_at"], "%Y-%m-%d %H:%M:%S")
            + timedelta(hours=int(candidate["ttl_hours"]))
        ).strftime("%Y-%m-%d %H:%M:%S")

    cursor = conn.execute(
        "INSERT INTO tasks "
        "(title, idea_summary, content_type, status, feasibility_score, "
        "score_breakdown, target_desc, expire_at) "
        "VALUES (?, ?, ?, 'todo', ?, ?, ?, ?)",
        (
            gen["title"],
            gen["idea_summary"],
            gen["content_type"],
            feasibility,
            score_breakdown_json,
            candidate["title"],
            expire_at,
        ),
    )
    task_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO task_links (task_id, hot_item_id) VALUES (?, ?)",
        (task_id, candidate["hotspot_id"]),
    )
    tag_ids = upsert_by_names(conn, gen["tags"])
    for tag_id in tag_ids:
        conn.execute(
            "INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)",
            (task_id, tag_id),
        )
    conn.commit()

    idea_path = Path(base_path) / "outputs" / "tasks" / str(task_id) / "idea.md"
    idea_path.parent.mkdir(parents=True, exist_ok=True)
    idea_path.write_text(gen["full_idea"], encoding="utf-8")
    return task_id


_CONTENT_TYPES = ("article", "video_script", "tweet", "newsletter")
_STATUSES = ("todo", "waiting", "in_progress", "done")
_TASK_FIELDS = (
    "id",
    "title",
    "idea_summary",
    "ai_summary",
    "content_type",
    "status",
    "feasibility_score",
    "score_breakdown",
    "target_desc",
    "expire_at",
    "token_used",
    "fail_count",
    "last_fail_reason",
    "redo_note",
    "notes",
    "created_at",
    "updated_at",
    "completed_at",
)
_PATCH_FIELDS = (
    "title",
    "idea_summary",
    "ai_summary",
    "content_type",
    "feasibility_score",
    "score_breakdown",
    "target_desc",
    "notes",
    "expire_at",
)


def _task_not_found(task_id: int) -> AppError:
    return AppError(404, NOT_FOUND, f"Task not found: {task_id}")


def _parse_score_breakdown(value) -> dict | None:
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            return None
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _serialize_task(row, tags: list[dict] | None = None) -> dict:
    task = {field: row[field] for field in _TASK_FIELDS}
    task["score_breakdown"] = _parse_score_breakdown(row["score_breakdown"])
    task["tags"] = tags or []
    return task


def _tags_for_tasks(conn, task_ids: list[int]) -> dict[int, list[dict]]:
    tags_by_task = {task_id: [] for task_id in task_ids}
    if not task_ids:
        return tags_by_task

    placeholders = ", ".join("?" for _ in task_ids)
    rows = conn.execute(
        "SELECT tt.task_id, t.id, t.name, t.color "
        "FROM tags t "
        "JOIN task_tags tt ON tt.tag_id = t.id "
        f"WHERE tt.task_id IN ({placeholders}) "
        "ORDER BY tt.task_id, t.id",
        task_ids,
    ).fetchall()
    for row in rows:
        tags_by_task[row["task_id"]].append(
            {"id": row["id"], "name": row["name"], "color": row["color"]}
        )
    return tags_by_task


def list_tasks(
    conn,
    *,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    q: str | None = None,
    tag: str | None = None,
) -> dict:
    page = max(1, page)
    size = min(max(1, size), 100)

    if status is not None and status not in _STATUSES:
        raise AppError(400, BAD_REQUEST, "Invalid task status")

    query = q.strip() if q is not None else ""
    if query and len(query) < 3:
        return {"items": [], "total": 0, "page": page, "size": size}

    joins = []
    filters = []
    values = []
    if query:
        joins.append("JOIN tasks_fts ON tasks_fts.rowid = t.id")
        filters.append("tasks_fts MATCH ?")
        values.append('"' + query.replace('"', '""') + '"')
    if tag is not None:
        joins.extend(
            [
                "JOIN task_tags filter_tt ON filter_tt.task_id = t.id",
                "JOIN tags filter_tag ON filter_tag.id = filter_tt.tag_id",
            ]
        )
        filters.append("filter_tag.name = ?")
        values.append(tag)
    if status is not None:
        filters.append("t.status = ?")
        values.append(status)

    join_sql = " " + " ".join(joins) if joins else ""
    where_sql = f" WHERE {' AND '.join(filters)}" if filters else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM tasks t{join_sql}{where_sql}",
        values,
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT t.* FROM tasks t{join_sql}{where_sql} "
        "ORDER BY t.updated_at DESC, t.id DESC LIMIT ? OFFSET ?",
        [*values, size, (page - 1) * size],
    ).fetchall()
    tags_by_task = _tags_for_tasks(conn, [row["id"] for row in rows])
    return {
        "items": [
            _serialize_task(row, tags_by_task.get(row["id"], [])) for row in rows
        ],
        "total": total,
        "page": page,
        "size": size,
    }


def get_task(conn, task_id: int) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise _task_not_found(task_id)

    tags = _tags_for_tasks(conn, [task_id])[task_id]
    task = _serialize_task(row, tags)
    hotspot_rows = conn.execute(
        "SELECT h.id, h.title, h.url, h.collected_date "
        "FROM hot_items h "
        "JOIN task_links tl ON tl.hot_item_id = h.id "
        "WHERE tl.task_id = ? "
        "ORDER BY h.id",
        (task_id,),
    ).fetchall()
    output_row = conn.execute(
        "SELECT COUNT(*) AS version_count, MAX(version) AS latest_version "
        "FROM outputs WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    task["hotspots"] = [dict(hotspot_row) for hotspot_row in hotspot_rows]
    task["output"] = {
        "has_output": output_row["version_count"] > 0,
        "latest_version": output_row["latest_version"],
        "version_count": output_row["version_count"],
        "ai_summary": row["ai_summary"],
    }
    return task


def create_task(conn, payload: dict) -> dict:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        raise AppError(400, BAD_REQUEST, "Task title is required")

    content_type = payload.get("content_type", "article")
    if content_type not in _CONTENT_TYPES:
        raise AppError(400, BAD_REQUEST, "Invalid content type")

    hotspot_id = payload.get("hotspot_id")
    if hotspot_id is not None:
        hotspot = conn.execute(
            "SELECT 1 FROM hot_items WHERE id = ?", (hotspot_id,)
        ).fetchone()
        if hotspot is None:
            raise AppError(400, BAD_REQUEST, f"Hotspot not found: {hotspot_id}")

    score_breakdown = payload.get("score_breakdown")
    if score_breakdown is None:
        score_breakdown = {}
    cursor = conn.execute(
        "INSERT INTO tasks "
        "(title, idea_summary, content_type, status, feasibility_score, "
        "score_breakdown, target_desc, expire_at, notes) "
        "VALUES (?, ?, ?, 'todo', ?, ?, ?, ?, ?)",
        (
            title,
            payload.get("idea_summary", ""),
            content_type,
            payload.get("feasibility_score", 0),
            json.dumps(score_breakdown, ensure_ascii=False),
            payload.get("target_desc", ""),
            payload.get("expire_at"),
            payload.get("notes", ""),
        ),
    )
    task_id = cursor.lastrowid
    if hotspot_id is not None:
        conn.execute(
            "INSERT INTO task_links (task_id, hot_item_id) VALUES (?, ?)",
            (task_id, hotspot_id),
        )
    conn.commit()
    return get_task(conn, task_id)


def update_task(conn, task_id: int, payload: dict) -> dict:
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise _task_not_found(task_id)
    if not payload:
        return get_task(conn, task_id)

    content_type = payload.get("content_type")
    if "content_type" in payload and content_type not in _CONTENT_TYPES:
        raise AppError(400, BAD_REQUEST, "Invalid content type")

    assignments = []
    values = []
    for field in _PATCH_FIELDS:
        if field not in payload:
            continue
        value = payload[field]
        if field == "score_breakdown":
            value = json.dumps(value, ensure_ascii=False)
        assignments.append(f"{field} = ?")
        values.append(value)

    if not assignments:
        return get_task(conn, task_id)
    conn.execute(
        f"UPDATE tasks SET {', '.join(assignments)}, updated_at = datetime('now') "
        "WHERE id = ?",
        [*values, task_id],
    )
    conn.commit()
    return get_task(conn, task_id)


def delete_task(conn, task_id: int, base_path: str) -> None:
    row = conn.execute(
        "SELECT status FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise _task_not_found(task_id)
    if row["status"] == "in_progress":
        raise AppError(409, "TASK_IN_PROGRESS", "In-progress tasks cannot be deleted")

    conn.execute("DELETE FROM outputs WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM task_tags WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM task_links WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()

    shutil.rmtree(
        os.path.join(base_path, "outputs", "tasks", str(task_id)),
        ignore_errors=True,
    )


def move_task(conn, task_id: int, to_status: str) -> dict:
    if to_status not in _STATUSES:
        raise AppError(400, BAD_REQUEST, "Invalid task status")
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise _task_not_found(task_id)

    allowed_sources = {
        "todo": ("waiting", "done"),
        "waiting": ("todo", "in_progress"),
        "in_progress": ("todo", "waiting"),
        "done": ("todo", "waiting", "in_progress"),
    }[to_status]
    placeholders = ", ".join("?" for _ in allowed_sources)
    cursor = conn.execute(
        "UPDATE tasks SET status = ?, updated_at = datetime('now'), "
        "completed_at = CASE WHEN ? = 'done' THEN datetime('now') ELSE NULL END "
        f"WHERE id = ? AND status IN ({placeholders})",
        (to_status, to_status, task_id, *allowed_sources),
    )
    if cursor.rowcount == 0:
        raise AppError(
            409,
            "INVALID_STATUS_TRANSITION",
            f"Task cannot move to {to_status}",
        )
    conn.commit()
    return get_task(conn, task_id)


def redo_task(conn, task_id: int, note: str | None = None) -> dict:
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise _task_not_found(task_id)

    redo_note = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if note:
        redo_note += f" {note}"
    cursor = conn.execute(
        "UPDATE tasks SET status = 'waiting', fail_count = 0, redo_note = ?, "
        "completed_at = NULL, updated_at = datetime('now') "
        "WHERE id = ? AND (status = 'done' OR fail_count > 0)",
        (redo_note, task_id),
    )
    if cursor.rowcount == 0:
        raise AppError(409, "REDO_NOT_ALLOWED", "Task cannot be redone")
    conn.commit()
    return get_task(conn, task_id)


def reset_failures(conn, task_id: int) -> dict:
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise _task_not_found(task_id)

    cursor = conn.execute(
        "UPDATE tasks SET fail_count = 0, updated_at = datetime('now') "
        "WHERE id = ? AND fail_count > 0",
        (task_id,),
    )
    if cursor.rowcount == 0:
        raise AppError(409, "NO_FAILURES", "Task has no failures to reset")
    conn.commit()
    return get_task(conn, task_id)


def set_task_tags(conn, task_id: int, names: list[str]) -> dict:
    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise _task_not_found(task_id)

    tag_ids = upsert_by_names(conn, names)
    conn.execute("DELETE FROM task_tags WHERE task_id = ?", (task_id,))
    for tag_id in tag_ids:
        conn.execute(
            "INSERT OR IGNORE INTO task_tags (task_id, tag_id) VALUES (?, ?)",
            (task_id, tag_id),
        )
    conn.commit()
    return get_task(conn, task_id)
