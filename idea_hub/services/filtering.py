"""Filtering helpers for collected raw items."""

import sqlite3

from ..collectors.base import RawItem


def apply_keywords_filter(items: list[RawItem], keywords: str) -> list[RawItem]:
    """Keep items whose titles contain at least one configured keyword."""
    tokens = [token.strip() for token in keywords.split(",") if token.strip()]
    if not tokens:
        return items
    return [item for item in items if any(token in item.title for token in tokens)]


def dedup_by_url(conn: sqlite3.Connection, items: list[RawItem]) -> list[RawItem]:
    """Remove existing and repeated non-empty ``(source_id, url)`` pairs."""
    existing = {
        (row[0], row[1])
        for row in conn.execute("SELECT source_id, url FROM hot_items").fetchall()
    }
    seen = set(existing)
    kept: list[RawItem] = []
    for item in items:
        if not item.url:
            kept.append(item)
            continue
        key = (item.source_id, item.url)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def truncate_snapshot(text: str, max_len: int = 2000) -> str:
    """Limit a content snapshot to ``max_len`` characters."""
    if len(text) <= max_len:
        return text
    return text[:max_len]
