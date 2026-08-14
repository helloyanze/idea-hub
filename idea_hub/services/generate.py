"""生成任务候选热点的查询服务。"""

import json

from .settings import get_all


def _parse_score_breakdown(value) -> dict:
    """将评分明细解析为字典，异常数据降级为空字典。"""
    try:
        score_breakdown = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return score_breakdown if isinstance(score_breakdown, dict) else {}


def get_candidates(
    conn,
    count: int | None = None,
    hotspot_ids: list[int] | None = None,
) -> list[dict]:
    """返回满足生成条件且尚未关联任务的热点。"""
    if hotspot_ids is not None and not hotspot_ids:
        return []

    settings = get_all(conn)
    score_threshold = settings.get("score_todo_threshold", 8)
    limit = count if count is not None else settings.get("generate_count", 10)

    filters = [
        "h.verdict = 'admit'",
        "(h.final_score >= ? OR h.final_score IS NULL OR h.final_score = 0)",
        "NOT EXISTS (SELECT 1 FROM task_links tl WHERE tl.hot_item_id = h.id)",
        "(s.ttl_hours IS NULL OR datetime(h.collected_at, '+' || s.ttl_hours || ' hours') > datetime('now'))",
    ]
    values = [score_threshold]

    if hotspot_ids is not None:
        placeholders = ", ".join("?" for _ in hotspot_ids)
        filters.append(f"h.id IN ({placeholders})")
        values.extend(hotspot_ids)

    rows = conn.execute(
        "SELECT h.id AS hotspot_id, h.title, h.url, h.source_id, "
        "h.collected_at, s.ttl_hours, h.final_score, h.score_breakdown "
        "FROM hot_items h JOIN sources s ON s.id = h.source_id "
        f"WHERE {' AND '.join(filters)} "
        "ORDER BY "
        "CASE WHEN h.final_score IS NULL OR h.final_score = 0 THEN 1 ELSE 0 END, "
        "CASE WHEN h.final_score IS NOT NULL AND h.final_score != 0 THEN h.final_score END DESC, "
        "CASE WHEN h.final_score IS NULL OR h.final_score = 0 THEN h.collected_at END DESC, "
        "h.id DESC LIMIT ?",
        [*values, limit],
    ).fetchall()

    return [
        {
            "hotspot_id": row["hotspot_id"],
            "title": row["title"],
            "url": row["url"],
            "source_id": row["source_id"],
            "collected_at": row["collected_at"],
            "ttl_hours": row["ttl_hours"],
            "final_score": row["final_score"],
            "score_breakdown": _parse_score_breakdown(row["score_breakdown"]),
        }
        for row in rows
    ]
