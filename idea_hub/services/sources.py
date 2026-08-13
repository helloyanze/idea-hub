"""Persistence and execution helpers for source configuration."""

import json

from ..collectors import collector_registry
from ..errors import AppError, NOT_FOUND


_FIELDS = (
    "type",
    "name",
    "url",
    "enabled",
    "items_path",
    "title_field",
    "keywords",
    "ttl_hours",
    "channel_config",
)


def _serialize(row) -> dict:
    """Convert a sqlite row into the public source representation."""
    result = dict(row)
    result["enabled"] = bool(result.get("enabled"))
    try:
        parsed = json.loads(result.get("channel_config") or "{}")
        result["channel_config"] = parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        result["channel_config"] = {}
    return result


def _source_not_found(source_id) -> AppError:
    return AppError(404, NOT_FOUND, f"Source not found: {source_id}")


def list_sources(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
    return [_serialize(row) for row in rows]


def create_source(conn, payload: dict) -> dict:
    source_type = payload["type"]
    if source_type not in collector_registry:
        raise AppError(
            400,
            "UNKNOWN_SOURCE_TYPE",
            f"Unknown source type: {source_type}",
        )
    values = (
        source_type,
        payload["name"],
        payload["url"],
        1 if payload["enabled"] else 0,
        payload["items_path"],
        payload["title_field"],
        payload["keywords"],
        payload["ttl_hours"],
        json.dumps(payload["channel_config"]),
    )
    cursor = conn.execute(
        "INSERT INTO sources "
        "(type, name, url, enabled, items_path, title_field, keywords, ttl_hours, channel_config) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        values,
    )
    conn.commit()
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _serialize(row)


def update_source(conn, source_id, payload: dict) -> dict:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise _source_not_found(source_id)

    assignments = []
    values = []
    for field in _FIELDS:
        value = payload.get(field)
        if value is None:
            continue
        if field == "enabled":
            value = 1 if value else 0
        elif field == "channel_config":
            value = json.dumps(value)
        elif field == "type" and value not in collector_registry:
            raise AppError(
                400,
                "UNKNOWN_SOURCE_TYPE",
                f"Unknown source type: {value}",
            )
        assignments.append(f"{field} = ?")
        values.append(value)

    if assignments:
        values.append(source_id)
        conn.execute(
            f"UPDATE sources SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        conn.commit()
    updated = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return _serialize(updated)


def toggle_source(conn, source_id) -> dict:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise _source_not_found(source_id)
    conn.execute(
        "UPDATE sources SET enabled = ? WHERE id = ?",
        (0 if row["enabled"] else 1, source_id),
    )
    conn.commit()
    updated = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return _serialize(updated)


def delete_source(conn, source_id) -> None:
    row = conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise _source_not_found(source_id)
    item_count = conn.execute(
        "SELECT COUNT(*) FROM hot_items WHERE source_id = ?", (source_id,)
    ).fetchone()[0]
    if item_count > 0:
        raise AppError(
            409,
            "SOURCE_HAS_ITEMS",
            f"Source has {item_count} hot items",
        )
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()


def test_source(conn, source_id) -> dict:
    row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise _source_not_found(source_id)
    source_row = dict(row)
    collector_class = collector_registry.get(source_row["type"])
    if collector_class is None:
        return {
            "ok": False,
            "item_count": 0,
            "sample_items": [],
            "error": f"unknown source type: {source_row['type']}",
        }
    try:
        items = collector_class(source_row).fetch()
        return {
            "ok": True,
            "item_count": len(items),
            "sample_items": [
                {
                    "title": item.title,
                    "url": item.url,
                    "content_snapshot": item.content_snapshot,
                }
                for item in items[:5]
            ],
        }
    except Exception as exc:
        return {
            "ok": False,
            "item_count": 0,
            "sample_items": [],
            "error": str(exc),
        }
