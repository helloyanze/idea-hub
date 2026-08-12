"""Structured read and orchestration services for the Idea Hub menu API."""

import sqlite3

from idea_hub import collectors, db, models


def _page_args(page, page_size):
    if page < 1:
        raise ValueError("page must be >= 1")
    if page_size < 1:
        raise ValueError("page_size must be >= 1")
    return page, page_size


def get_hotspot_summary(conn: sqlite3.Connection, page: int, page_size: int):
    page, page_size = _page_args(page, page_size)
    total = conn.execute("SELECT COUNT(*) FROM hot_items").fetchone()[0]
    offset = (page - 1) * page_size
    rows = conn.execute(
        "SELECT h.id, h.source_id, s.name AS source_name, h.title, h.url, "
        "h.content_snapshot, h.collected_at, h.final_score, h.review_status, "
        "(SELECT COUNT(*) FROM task_links tl WHERE tl.hot_item_id=h.id) AS linked_task_count "
        "FROM hot_items h JOIN sources s ON s.id=h.source_id "
        "ORDER BY h.collected_at DESC, h.id DESC LIMIT ? OFFSET ?",
        (page_size, offset),
    ).fetchall()
    return {"items": [dict(row) for row in rows], "total": total,
            "page": page, "page_size": page_size}


def get_queue_summary(conn: sqlite3.Connection):
    return models.stats(conn)


def get_queue_items(conn: sqlite3.Connection, status: str, page: int, page_size: int):
    if status not in models.STATUSES:
        raise ValueError(f"bad status {status}")
    page, page_size = _page_args(page, page_size)
    total = conn.execute("SELECT COUNT(*) FROM tasks WHERE status=?", (status,)).fetchone()[0]
    offset = (page - 1) * page_size
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status=? ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
        (status, page_size, offset),
    ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["tags"] = models.list_task_tags(conn, item["id"])
        items.append(item)
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def collect_ideas(db_path, base_path):
    """Collect hotspots using the existing collector pipeline.

    ``base_path`` is accepted for symmetry with generation orchestration and
    future agent runners; collection itself does not write relative artifacts.
    """
    del base_path
    conn = db.connect(str(db_path))
    try:
        db.init_schema(conn)
        return collectors.collect_all(conn)
    finally:
        conn.close()


def generate_ideas(db_path, base_path):
    """Describe the available agent handoff without pretending to call an LLM."""
    del base_path
    conn = db.connect(str(db_path))
    try:
        db.init_schema(conn)
        candidate_count = conn.execute(
            "SELECT COUNT(*) FROM hot_items h "
            "WHERE date(h.collected_at)=date('now') "
            "AND NOT EXISTS (SELECT 1 FROM task_links tl WHERE tl.hot_item_id=h.id)"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "status": "needs-agent",
        "generated": 0,
        "candidate_count": candidate_count,
        "message": "Idea generation requires the Hermes agent workflow.",
        "next_step": "Run candidates, generate ideas with Hermes, then import-ideas.",
    }
