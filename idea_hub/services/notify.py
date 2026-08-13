"""Notification persistence service."""


NOTIFICATION_TYPES = {
    "collect_done",
    "generate_done",
    "execute_done",
    "job_failed",
    "task_expired",
    "budget_exceeded",
    "discard_cleaned",
}
NOTIFICATION_LEVELS = {"info", "warn", "error"}


def emit(
    conn,
    type: str,
    title: str,
    body: str,
    level: str,
    entity_type: str | None = None,
    entity_id: int | None = None,
) -> int:
    """Insert a notification and return its database ID."""
    if type not in NOTIFICATION_TYPES:
        raise ValueError(
            "Invalid notification type; expected one of: "
            + ", ".join(sorted(NOTIFICATION_TYPES))
        )
    if level not in NOTIFICATION_LEVELS:
        raise ValueError(
            "Invalid notification level; expected one of: "
            + ", ".join(sorted(NOTIFICATION_LEVELS))
        )

    cursor = conn.execute(
        """
        INSERT INTO notifications
            (type, title, body, level, entity_type, entity_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (type, title, body, level, entity_type, entity_id),
    )
    conn.commit()
    return cursor.lastrowid
