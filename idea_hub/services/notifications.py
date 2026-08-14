"""Persistence helpers for notification queries."""

from ..errors import AppError, BAD_REQUEST, NOT_FOUND
from .notify import NOTIFICATION_LEVELS, NOTIFICATION_TYPES


_SELECT_FIELDS = (
    "id, type, title, body, level, entity_type, entity_id, is_read, created_at"
)


def _serialize(row) -> dict:
    return {key: row[key] for key in row.keys()}


def list_notifications(
    conn,
    page: int = 1,
    size: int = 20,
    level: str | None = None,
    type: str | None = None,
    unread_only: bool = False,
) -> dict:
    page = max(1, page)
    size = min(max(1, size), 100)

    if level is not None and level not in NOTIFICATION_LEVELS:
        raise AppError(400, BAD_REQUEST, "Invalid notification level")
    if type is not None and type not in NOTIFICATION_TYPES:
        raise AppError(400, BAD_REQUEST, "Invalid notification type")

    filters = []
    values = []
    if level is not None:
        filters.append("level = ?")
        values.append(level)
    if type is not None:
        filters.append("type = ?")
        values.append(type)
    if unread_only:
        filters.append("is_read = 0")

    where = f" WHERE {' AND '.join(filters)}" if filters else ""
    total = conn.execute(
        f"SELECT COUNT(*) FROM notifications{where}",
        values,
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT {_SELECT_FIELDS} FROM notifications{where} "
        "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        [*values, size, (page - 1) * size],
    ).fetchall()
    return {
        "items": [_serialize(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


def unread_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE is_read = 0"
    ).fetchone()[0]


def mark_read(conn, notification_id: int) -> dict:
    row = conn.execute(
        "SELECT 1 FROM notifications WHERE id = ?",
        (notification_id,),
    ).fetchone()
    if row is None:
        raise AppError(
            404,
            NOT_FOUND,
            f"Notification not found: {notification_id}",
        )

    conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ?",
        (notification_id,),
    )
    conn.commit()
    return {"id": notification_id, "is_read": True}


def read_all(conn) -> dict:
    cursor = conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE is_read = 0"
    )
    conn.commit()
    return {"updated": cursor.rowcount}
