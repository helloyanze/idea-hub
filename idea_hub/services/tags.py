"""标签创建与复用服务。"""


TAG_COLORS = ["#3b82f6", "#ef4444", "#22c55e", "#f59e0b", "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16"]


def upsert_by_names(conn, names: list[str]) -> list[int]:
    """按名称创建或复用标签，并返回对应 ID。"""
    cleaned_names = []
    for name in names:
        if not isinstance(name, str):
            continue
        name = name.strip()
        if name and name not in cleaned_names:
            cleaned_names.append(name)

    if not cleaned_names:
        return []

    total = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    tag_ids = []
    for name in cleaned_names:
        row = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()
        if row is not None:
            tag_ids.append(row[0])
            continue

        color = TAG_COLORS[total % len(TAG_COLORS)]
        cursor = conn.execute(
            "INSERT INTO tags (name, color) VALUES (?, ?)", (name, color)
        )
        total += 1
        tag_ids.append(cursor.lastrowid)

    return tag_ids
