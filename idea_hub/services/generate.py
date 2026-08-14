"""生成任务候选热点的查询服务。"""

import json
import logging

from .llm import chat_json
from .settings import get_all


logger = logging.getLogger(__name__)


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


VALID_CONTENT_TYPES = ["article", "video_script", "tweet", "newsletter"]


def build_generate_prompt(candidates: list[dict]) -> list[dict]:
    """构建内容生成任务所需的 OpenAI 风格消息。"""
    system = (
        "你是中文内容策划师，擅长把候选热点转化为有事实依据、可执行且有传播力的内容构思。\n"
        "请为每个候选热点生成一个构思对象，必须包含以下字段：title、idea_summary、full_idea、content_type、tags。\n"
        "content_type 只能是 article、video_script、tweet、newsletter；tags 必须是 2-5 个中文标签；"
        "full_idea 必须是完整的 Markdown 形式创意草稿。\n"
        "只输出 JSON 数组，不要输出任何额外文字。"
    )
    serialized_candidates = json.dumps(candidates, ensure_ascii=False, indent=2)
    user = (
        "以下是候选热点列表。请按列表原顺序，为每个候选热点生成一个构思对象，"
        "每个候选对应一个对象，不要遗漏或改变顺序。\n"
        f"{serialized_candidates}\n"
        "只输出 JSON 数组，不要输出数组之外的任何内容。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _clean_tags(tags) -> list[str]:
    """清理标签：去除空值、去重并保留前五个。"""
    if not isinstance(tags, list):
        return []
    cleaned = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        tag = tag.strip()
        if tag and tag not in cleaned:
            cleaned.append(tag)
        if len(cleaned) == 5:
            break
    return cleaned


def _normalize_gen(item: dict) -> dict:
    """将单条生成结果规范化为固定字段结构。"""
    content_type = item.get("content_type")
    return {
        "title": str(item.get("title") or "未命名构思"),
        "idea_summary": str(item.get("idea_summary") or ""),
        "full_idea": str(item.get("full_idea") or ""),
        "content_type": content_type if content_type in VALID_CONTENT_TYPES else "article",
        "tags": _clean_tags(item.get("tags")),
    }


def generate_one(
    candidates: list[dict],
    api_key: str,
    timeout: float = 90,
    max_retries: int = 2,
) -> list[dict]:
    """为候选热点生成并规范化内容构思。"""
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置：生成任务需要 LLM key，无法降级执行")

    messages = build_generate_prompt(candidates)
    result = chat_json(messages, api_key, timeout=timeout, max_retries=max_retries)
    if not isinstance(result, list):
        raise ValueError("generate 输出不是 JSON 数组")
    return [_normalize_gen(item) for item in result if isinstance(item, dict)]
