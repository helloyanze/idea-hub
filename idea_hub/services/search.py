"""Unified full-text search helpers."""


_ENTITY_ORDER = {"hot_item": 0, "task": 1, "output": 2}


def _snippet(title: str, body: str, query: str) -> str:
    title = title or ""
    body = body or ""
    haystack = title + "\n" + body
    position = haystack.find(query)
    if position >= 0:
        start = max(0, position - 50)
        end = position + 50 + len(query)
        return haystack[start:end].strip()
    if body:
        return body[:100]
    return title[:100]


def _fetch_matches(conn, sql: str, match: str, limit: int) -> list[dict]:
    return [
        {
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "title": row["title"] or "",
            "body": row["body"] or "",
            "score": float(row["score"]),
        }
        for row in conn.execute(sql, (match, limit)).fetchall()
    ]


def search(conn, q: str, page: int = 1, size: int = 20) -> dict:
    page = max(1, page)
    size = min(max(1, size), 100)
    query = q.strip()
    if len(query) < 3:
        return {"items": [], "total": 0, "page": page, "size": size}

    match = '"' + query.replace('"', '""') + '"'
    limit = page * size

    hot_count = conn.execute(
        "SELECT COUNT(*) FROM hot_items_fts WHERE hot_items_fts MATCH ?",
        (match,),
    ).fetchone()[0]
    task_count = conn.execute(
        "SELECT COUNT(*) FROM tasks_fts WHERE tasks_fts MATCH ?",
        (match,),
    ).fetchone()[0]
    output_count = conn.execute(
        "SELECT COUNT(*) FROM outputs_fts WHERE outputs_fts MATCH ?",
        (match,),
    ).fetchone()[0]

    pool = []
    pool.extend(
        _fetch_matches(
            conn,
            "SELECT 'hot_item' AS entity_type, h.id AS entity_id, "
            "h.title AS title, h.content_snapshot AS body, "
            "bm25(hot_items_fts) AS score "
            "FROM hot_items_fts "
            "JOIN hot_items h ON h.id = hot_items_fts.rowid "
            "WHERE hot_items_fts MATCH ? "
            "ORDER BY bm25(hot_items_fts) ASC LIMIT ?",
            match,
            limit,
        )
    )
    pool.extend(
        _fetch_matches(
            conn,
            "SELECT 'task' AS entity_type, t.id AS entity_id, "
            "t.title AS title, "
            "CASE WHEN t.ai_summary <> '' THEN t.ai_summary ELSE t.idea_summary END "
            "AS body, bm25(tasks_fts) AS score "
            "FROM tasks_fts "
            "JOIN tasks t ON t.id = tasks_fts.rowid "
            "WHERE tasks_fts MATCH ? "
            "ORDER BY bm25(tasks_fts) ASC LIMIT ?",
            match,
            limit,
        )
    )
    pool.extend(
        _fetch_matches(
            conn,
            "SELECT 'output' AS entity_type, o.task_id AS entity_id, "
            "t.title AS title, o.content AS body, bm25(outputs_fts) AS score "
            "FROM outputs_fts "
            "JOIN outputs o ON o.id = outputs_fts.rowid "
            "JOIN tasks t ON t.id = o.task_id "
            "WHERE outputs_fts MATCH ? "
            "ORDER BY bm25(outputs_fts) ASC LIMIT ?",
            match,
            limit,
        )
    )
    pool.sort(key=lambda item: (_ENTITY_ORDER[item["entity_type"]], item["score"]))

    start = (page - 1) * size
    selected = pool[start : page * size]
    items = [
        {
            "entity_type": item["entity_type"],
            "entity_id": item["entity_id"],
            "title": item["title"],
            "snippet": _snippet(item["title"], item["body"], query),
            "score": item["score"],
        }
        for item in selected
    ]
    return {
        "items": items,
        "total": hot_count + task_count + output_count,
        "page": page,
        "size": size,
    }
