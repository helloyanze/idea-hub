"""Persistence helpers for hotspot queries."""

import json

from ..errors import AppError, BAD_REQUEST, NOT_FOUND


_SELECT = (
    "SELECT h.*, s.name AS source_name, "
    "(SELECT COUNT(*) FROM task_links t WHERE t.hot_item_id = h.id) "
    "AS linked_task_count "
    "FROM hot_items h "
    "JOIN sources s ON s.id = h.source_id"
)


def _serialize(row) -> dict:
    """Convert a sqlite row into the public hotspot representation."""
    try:
        score_breakdown = json.loads(row["score_breakdown"])
        if not isinstance(score_breakdown, dict):
            score_breakdown = None
    except (TypeError, ValueError, json.JSONDecodeError):
        score_breakdown = None

    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "title": row["title"],
        "url": row["url"],
        "content_snapshot": row["content_snapshot"],
        "final_score": row["final_score"],
        "score_breakdown": score_breakdown,
        "verdict": row["verdict"],
        "collected_date": row["collected_date"],
        "source_name": row["source_name"],
        "linked_task_count": row["linked_task_count"],
    }


def list_hotspots(
    conn,
    page: int = 1,
    size: int = 20,
    source_id: int | None = None,
    verdict: str | None = None,
    q: str | None = None,
) -> dict:
    page = max(1, page)
    size = min(max(1, size), 100)

    if verdict is not None and verdict not in ("admit", "discard"):
        raise AppError(400, BAD_REQUEST, "Invalid verdict")

    query = q.strip() if q is not None else ""
    if query and len(query) < 3:
        return {"items": [], "total": 0, "page": page, "size": size}

    joins = ""
    filters = []
    values = []
    if query:
        joins = " JOIN hot_items_fts ON hot_items_fts.rowid = h.id"
        filters.append("hot_items_fts MATCH ?")
        values.append('"' + query.replace('"', '""') + '"')
    if source_id is not None:
        filters.append("h.source_id = ?")
        values.append(source_id)
    if verdict is not None:
        filters.append("h.verdict = ?")
        values.append(verdict)

    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM hot_items h "
        f"JOIN sources s ON s.id = h.source_id{joins}{where}",
        values,
    ).fetchone()[0]
    rows = conn.execute(
        f"{_SELECT}{joins}{where} "
        "ORDER BY h.collected_date DESC, h.id DESC LIMIT ? OFFSET ?",
        [*values, size, (page - 1) * size],
    ).fetchall()
    return {
        "items": [_serialize(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


def get_hotspot(conn, item_id: int) -> dict:
    row = conn.execute(
        f"{_SELECT} WHERE h.id = ?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, NOT_FOUND, f"Hotspot not found: {item_id}")
    return _serialize(row)
