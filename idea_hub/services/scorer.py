"""Idea Hub v2 评分服务：规则过滤、LLM 批量评分与两档分流。"""

from dataclasses import dataclass, field
import json
import logging
import math
import re

import httpx

from ..collectors.base import RawItem


logger = logging.getLogger(__name__)


LLM_URL = "https://api.deepseek.com/chat/completions"
LLM_MODEL = "deepseek-chat"
LLM_TIMEOUT = 90.0
LLM_MAX_RETRIES = 2

# 以下规则词表从 v1 idea_hub/scorer.py 移植，规则层不消耗 token。
CLICKBAIT_WORDS = [
    "震惊", "颠覆", "史上最", "炸裂", "重磅", "突发", "紧急", "速看", "疯传", "刷屏",
    "惊呆", "吓尿", "绝了", "逆天", "封神", "王炸", "不看后悔", "震惊全场", "颠覆认知",
    "史诗级", "核弹级", "竟然", "万万没想到", "彻底沦陷", "全崩了",
]
AD_WORDS = [
    "加微信", "扫码", "限时优惠", "点击购买", "立即下单", "咨询客服", "私聊",
    "联系方式", "原价", "现价", "仅剩", "亏本清仓", "全网最低", "领取福利",
]
RUMOR_KEYWORDS = ["辟谣", "不实", "更正", "虚假", "谣言", "已被证实为假"]
BLOCKLIST: list[str] = []
CLICKBAIT_THRESHOLD = 2
AD_THRESHOLD = 1

# 时效衰减参考实现（v1）：S_time = 100 × e^(-λ × 距发布小时数)。
# RawItem 无 collected_at 字段，本函数为规则层参考/预留，不参与本任务 verdict 计算。
TIME_LAMBDA = {
    "breaking_news": 0.10,
    "tech_trend": 0.02,
    "policy_data": 0.005,
}


def time_score_from_hours(age_hours, kind="tech_trend"):
    """时效衰减：age_hours 未知时返回 100（不惩罚）。"""
    if age_hours is None:
        return 100.0
    lam = TIME_LAMBDA.get(kind, TIME_LAMBDA["tech_trend"])
    return round(100.0 * math.exp(-lam * age_hours), 1)


def _domain_of(url):
    m = re.search(r"https?://([^/:]+)", url or "")
    return (m.group(1).lower() if m else "").removeprefix("www.")


def is_clickbait(title):
    """标题党：标题命中 CLICKBAIT_WORDS >= 2 词。"""
    return sum(1 for w in CLICKBAIT_WORDS if w in (title or "")) >= CLICKBAIT_THRESHOLD


def is_ad(title, snapshot=""):
    """广告/营销：标题+摘要命中 AD_WORDS >= 1 词。"""
    return sum(1 for w in AD_WORDS if w in (title or "") + " " + (snapshot or "")) >= AD_THRESHOLD


def has_rumor_marker(text):
    """疑似辟谣内容：命中 RUMOR_KEYWORDS 任一。"""
    return any(w in (text or "") for w in RUMOR_KEYWORDS)


def check_blocklist(url):
    """黑名单域名一票否决。"""
    d = _domain_of(url)
    return any(d == x or d.endswith("." + x) for x in BLOCKLIST)


def _rule_reason(item) -> str | None:
    """规则过滤层：返回否决原因；None = 通过规则层（0 token，参考 v1 规则列表）。"""
    if check_blocklist(item.url):
        return "来源黑名单"
    if is_clickbait(item.title):
        return "标题党"
    if is_ad(item.title, item.content_snapshot):
        return "广告/营销"
    if has_rumor_marker(item.title + " " + item.content_snapshot):
        return "疑似辟谣内容"
    return None


@dataclass
class ScoredItem(RawItem):
    """评分结果：RawItem + 总分 + 维度明细 + 两档 verdict。"""

    final_score: float | None = None
    score_breakdown: dict = field(default_factory=dict)
    verdict: str = "admit"


def _fallback(items):
    """降级：无 key 或 LLM 调用失败 → 全量 admit、评分字段为空（spec 5.4）。"""
    return [
        ScoredItem(title=it.title, url=it.url, content_snapshot=it.content_snapshot,
                   source_id=it.source_id)
        for it in items
    ]


def _parse_llm_array(raw):
    """宽松解析 LLM JSON 数组：剥 code fence、截取首个 [...] 块。解析失败抛 ValueError。"""
    if not raw:
        raise ValueError("empty LLM output")
    text = raw.strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    if m:
        text = m.group(1).strip()
    m2 = re.search(r"\[.*\]", text, re.S)
    if not m2:
        raise ValueError("no json array found")
    data = json.loads(m2.group(0))
    if not isinstance(data, list):
        raise ValueError("not a json array")
    return data


def _llm_score_batch(items, api_key, dimensions):
    """一次 DeepSeek 批量评分调用，返回 {index: {dim: score}}。失败抛异常由调用方重试。"""
    lines = "\n".join(
        f"{i + 1}. 标题：{it.title}\n   URL：{it.url}\n   摘要：{(it.content_snapshot or '')[:300]}"
        for i, it in enumerate(items))
    dims = "、".join(dimensions)
    system = (
        "你是信息质量评估器。对每条信息按给定维度打分，只输出 JSON 数组，不要任何解释。\n"
        f"维度：{dims}\n"
        "评分标准：0-10 整数；有具体数据/日期/可验证陈述=高分，纯观点/情绪/模糊表述=低分。\n"
        "输出格式："
        '[{"title": "原样标题", "dimension_scores": {"facts": 8, ...}, "final_score": 8}]'
    )
    user = (
        f"信息列表（共 {len(items)} 条，按编号对应输出数组顺序）：\n{lines}\n\n"
        "只输出 JSON 数组，不要输出数组以外的任何内容。"
    )
    resp = httpx.post(
        LLM_URL,
        timeout=LLM_TIMEOUT,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": LLM_MODEL, "temperature": 0,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    rows = _parse_llm_array(content)
    results = {}
    for i, it in enumerate(items):
        row = rows[i] if i < len(rows) and isinstance(rows[i], dict) else {}
        scores = row.get("dimension_scores") or {}
        results[i] = {
            dim: max(0, min(10, int(scores.get(dim, 0) or 0)))
            for dim in dimensions
        }
    return results


def score_items(items, api_key=None, dimensions=None, threshold=8):
    """批量评分：规则过滤（0 token）→ LLM 批量评分 → 等权聚合 + 两档分流。

    无 api_key 或 LLM 调用失败 → 降级：全部 verdict=admit、评分字段为空。
    threshold 为 score_todo_threshold（settings，默认 8），由调用方传入。
    """
    dimensions = dimensions or ["facts", "verification", "timeliness", "value"]
    if not api_key:
        return _fallback(items)

    output: list[ScoredItem | None] = [None] * len(items)
    llm_items, idx_map = [], []
    for i, it in enumerate(items):
        reason = _rule_reason(it)
        if reason:
            output[i] = ScoredItem(title=it.title, url=it.url,
                                   content_snapshot=it.content_snapshot,
                                   source_id=it.source_id,
                                   final_score=0, score_breakdown={},
                                   verdict="discard")
            continue
        idx_map.append(i)
        llm_items.append(it)

    if llm_items:
        last_err = None
        for attempt in range(LLM_MAX_RETRIES + 1):
            try:
                results = _llm_score_batch(llm_items, api_key, dimensions)
                break
            except Exception as exc:
                last_err = exc
        else:
            # 重试耗尽：降级为全量 admit，收集不中断
            logger.warning("LLM scoring failed, falling back to admit: %s", last_err)
            return _fallback(items)
        for pos, i in enumerate(idx_map):
            it = items[i]
            breakdown = results.get(pos, {})
            mean = sum(breakdown.values()) / len(dimensions) if breakdown else 0
            final_score = int(math.floor(mean + 0.5))
            output[i] = ScoredItem(
                title=it.title, url=it.url, content_snapshot=it.content_snapshot,
                source_id=it.source_id, final_score=final_score,
                score_breakdown=breakdown,
                verdict="admit" if final_score >= threshold else "discard",
            )

    return [item for item in output if item is not None]