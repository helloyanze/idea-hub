"""Settings persistence and type conversion helpers."""

import json
from typing import Any

from ..errors import AppError


PUBLIC_KEYS = (
    "score_todo_threshold",
    "collect_interval_hours",
    "daily_budget_tokens",
    "score_dimensions",
    "generate_count",
    "done_column_limit",
    "discard_retention_days",
)


def _convert_stored(value: str, value_type: str) -> Any:
    if value_type == "int":
        return int(value)
    if value_type == "json":
        return json.loads(value)
    if value_type == "float":
        return float(value)
    if value_type == "string":
        return str(value)
    raise ValueError(f"Unknown setting type: {value_type}")


def get_all(conn) -> dict:
    """Return all valid public settings with values converted by type."""
    settings = {}
    rows = conn.execute("SELECT key, value, value_type FROM settings").fetchall()
    public_keys = set(PUBLIC_KEYS)
    for row in rows:
        key = row["key"]
        value_type = row["value_type"]
        if key not in public_keys or value_type not in {"int", "json", "float", "string"}:
            continue
        try:
            settings[key] = _convert_stored(row["value"], value_type)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return settings


def _invalid_value(key: str) -> AppError:
    return AppError(
        status_code=400,
        code="INVALID_SETTING_VALUE",
        message=f"Invalid value for setting: {key}",
    )


def _convert_input(value: Any, value_type: str, key: str) -> tuple[Any, str]:
    try:
        if value_type == "int":
            if isinstance(value, int) and not isinstance(value, bool):
                converted = value
            elif isinstance(value, str):
                converted = int(value)
            else:
                raise ValueError
            return converted, str(converted)

        if value_type == "json":
            if not isinstance(value, (list, dict)):
                raise ValueError
            return value, json.dumps(value)

        if value_type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ValueError
            converted = float(value)
            return converted, str(converted)

        if value_type == "string":
            converted = str(value)
            return converted, converted
    except (TypeError, ValueError, OverflowError):
        raise _invalid_value(key) from None
    raise _invalid_value(key)


def update(conn, key: str, value: Any) -> dict:
    """Validate and persist one public setting."""
    if key not in PUBLIC_KEYS:
        raise AppError(
            status_code=400,
            code="UNKNOWN_SETTING",
            message=f"Unknown setting: {key}",
        )

    row = conn.execute("SELECT value_type FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise AppError(
            status_code=400,
            code="UNKNOWN_SETTING",
            message=f"Unknown setting: {key}",
        )

    converted, stored = _convert_input(value, row["value_type"], key)
    conn.execute("UPDATE settings SET value = ? WHERE key = ?", (stored, key))
    conn.commit()
    return {"key": key, "value": converted}
