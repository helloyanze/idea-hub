"""热点信息评分机制（v1，对应 docs/热点信息评分机制设计.md）。

三明治架构：
  规则过滤层（0 Token）→ 轻量 LLM 批量评分 → 加权聚合 + 阈值分流
核心原则：规则能做的绝不调用 LLM，LLM 只做规则无法判断的事。
"""

import json
import math
import os
import pathlib
import re
from datetime import datetime, timezone

# ---------------- 配置（可被 settings 覆盖的部分在 collect 时注入） ----------------

# 来源分级查表：域名 → 层级（S/A/B/C/D），未匹配默认 D
SOURCE_TIERS = {
    "S": {"score": 95, "domains": ["gov.cn", "sec.gov", "nature.com", "science.org", "reuters.com", "apnews.com", "bloomberg.com"]},
    "A": {"score": 80, "domains": ["36kr.com", "jiqizhixin.com", "spectrum.ieee.org", "theverge.com", "36kr.cn", "infoq.cn", "geekpark.net"]},
    "B": {"score": 65, "domains": ["github.com", "zhihu.com", "rsshub.app", "hacker-news.firebaseio.com", "news.ycombinator.com", "top.baidu.com"]},
    "C": {"score": 50, "domains": ["weibo.com", "xiaohongshu.com", "twitter.com", "x.com", "douyin.com", "bilibili.com"]},
    "D": {"score": 35, "domains": []},  # 默认
}

# 标题党词表（命中 >=2 触发）
CLICKBAIT_WORDS = [
    "震惊", "颠覆", "史上最", "炸裂", "重磅", "突发", "紧急", "速看", "疯传", "刷屏",
    "惊呆", "吓尿", "绝了", "逆天", "封神", "王炸", "不看后悔", "震惊全场", "颠覆认知",
    "史诗级", "核弹级", "竟然", "万万没想到", "彻底沦陷", "全崩了",
]
CLICKBAIT_THRESHOLD = 2

# 广告词表（命中 >=1 触发）
AD_WORDS = [
    "加微信", "扫码", "限时优惠", "点击购买", "立即下单", "咨询客服", "私聊",
    "联系方式", "原价", "现价", "仅剩", "亏本清仓", "全网最低", "领取福利",
]
AD_THRESHOLD = 1

# 黑名单域名
BLOCKLIST = []

# 辟谣关键词（交叉验证命中则丢弃；此处为规则层简化检查）
RUMOR_KEYWORDS = ["辟谣", "不实", "更正", "虚假", "谣言", "已被证实为假"]

# 时效衰减系数（按信息类型）
TIME_LAMBDA = {
    "breaking_news": 0.10,   # 突发新闻：24h 后约剩 9%
    "tech_trend": 0.02,      # 技术趋势：一周仍有价值
    "policy_data": 0.005,    # 政策/数据：长期有效
}

# 聚合权重与阈值（评分机制设计文档 §5）
WEIGHTS = {"source": 0.35, "factuality": 0.25, "verification": 0.25, "timeliness": 0.15}
THRESHOLD_AUTO = 75    # >= 75 自动收录
THRESHOLD_REVIEW = 55  # 55-74 待人工复核；< 55 丢弃
VERIFY_DEFAULT = 60    # 未触发交叉验证时的中性验证分

# LLM 批量评分（每条均摊 <55 Token）
LLM_URL = "https://api.deepseek.com/chat/completions"
LLM_MODEL = "deepseek-chat"
LLM_BATCH_SIZE = 8
LLM_SYSTEM_PROMPT = (
    "你是信息质量评估器。对以下每条信息，只输出两个0-10的整数，用逗号分隔，每行一条，不要任何解释。\n"
    "评分标准：\n"
    "- 第一个数(事实性): 有具体数据/日期/可验证陈述=高分，纯观点/情绪/模糊表述=低分\n"
    "- 第二个数(验证需求): 官方可信信息=1-3，行业传闻=4-6，突发事件/争议爆料=7-10"
)


# ---------------- 规则过滤层（0 Token） ----------------

def _domain_of(url):
    m = re.search(r"https?://([^/:]+)", url or "")
    return (m.group(1).lower() if m else "").removeprefix("www.")


def source_tier(url):
    """域名查表返回 (tier, score)。未匹配默认 D 级 35 分。"""
    d = _domain_of(url)
    for tier, cfg in SOURCE_TIERS.items():
        if any(d == x or d.endswith("." + x) for x in cfg["domains"]):
            return tier, cfg["score"]
    return "D", SOURCE_TIERS["D"]["score"]


def check_blocklist(url):
    """黑名单域名检查。命中返回 True（一票否决）。"""
    d = _domain_of(url)
    return any(d == x or d.endswith("." + x) for x in BLOCKLIST)


def count_hits(text, words):
    return sum(1 for w in words if w in (text or ""))


def is_clickbait(title):
    return count_hits(title, CLICKBAIT_WORDS) >= CLICKBAIT_THRESHOLD


def is_ad(title, snapshot=""):
    return count_hits(title + " " + snapshot, AD_WORDS) >= AD_THRESHOLD


def has_rumor_marker(text):
    return any(w in (text or "") for w in RUMOR_KEYWORDS)


def time_score_from_hours(age_hours, kind="tech_trend"):
    """S_time = 100 × e^(-λ × 距发布小时数)。age_hours 未知时返回 100（不惩罚）。"""
    if age_hours is None:
        return 100.0
    lam = TIME_LAMBDA.get(kind, TIME_LAMBDA["tech_trend"])
    return round(100.0 * math.exp(-lam * age_hours), 1)


def time_score_from_str(collected_at, now=None, kind="tech_trend"):
    """collected_at 为 UTC 'YYYY-MM-DD HH:MM:SS'（SQLite datetime('now')）。"""
    try:
        dt = datetime.strptime(collected_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 100.0
    now = now or datetime.now(timezone.utc)
    return time_score_from_hours(max(0, (now - dt).total_seconds() / 3600), kind)


# ---------------- 轻量 LLM 评分层 ----------------

def _llm_key():
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    # 兼容 idea-hub/.env 或 ~/.hermes/.env
    for p in (pathlib.Path(__file__).parent.parent / ".env", pathlib.Path.home() / ".hermes" / ".env"):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            continue
    return ""


def llm_score_batch(items, session=None, api_key=None, model=LLM_MODEL, timeout=60):
    """批量评分：输入 items（含 title/summary），输出 {index: (fact, verify_need)}。

    items 为列表，每项至少含 'title'；'summary' 可选。
    单批 LLM_BATCH_SIZE 条，每条输出 '事实性,验证需求' 一行。
    """
    import requests
    key = api_key or _llm_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置（环境变量或 .env）")
    s = session or requests
    results = {}
    for start in range(0, len(items), LLM_BATCH_SIZE):
        batch = items[start:start + LLM_BATCH_SIZE]
        lines = "\n".join(
            f"{i + 1}. {it.get('title', '')} - {it.get('summary', it.get('content_snapshot', ''))[:80]}"
            for i, it in enumerate(batch))
        resp = s.post(LLM_URL, timeout=timeout,
                      headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json={"model": model, "temperature": 0,
                            "messages": [{"role": "system", "content": LLM_SYSTEM_PROMPT},
                                         {"role": "user", "content": f"信息列表：\n{lines}\n\n输出格式（每行对应一条）：\n事实性,验证需求"}]})
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        for i, line in enumerate(text.splitlines()[:len(batch)]):
            nums = re.findall(r"\d+", line)
            if len(nums) >= 2:
                results[start + i] = (min(10, int(nums[0])), min(10, int(nums[1])))
    return results


# ---------------- 聚合与分流 ----------------

def compute_final(source_score, fact_score, verify_score, time_score):
    """FinalScore = 0.35×S_source + 0.25×(S_fact×10) + 0.25×S_verify + 0.15×S_time"""
    return round(
        WEIGHTS["source"] * source_score
        + WEIGHTS["factuality"] * (fact_score * 10)
        + WEIGHTS["verification"] * verify_score
        + WEIGHTS["timeliness"] * time_score, 1)


def classify(final_score):
    """>=75 收录 / 55-74 复核 / <55 丢弃。"""
    if final_score >= THRESHOLD_AUTO:
        return "collected"
    if final_score >= THRESHOLD_REVIEW:
        return "review"
    return "discarded"


def score_item(title, url, snapshot="", collected_at=None, fact=None, verify_need=None, kind="tech_trend"):
    """单条完整评分。fact/verify_need 为 LLM 输出（0-10），None 时按中性处理。

    返回 dict：各维度分、final_score、review_status、reason（规则层否决原因）。
    """
    # 一票否决（规则层）
    if check_blocklist(url):
        return {"review_status": "discarded", "final_score": 0, "reason": "来源黑名单"}
    if is_clickbait(title):
        return {"review_status": "discarded", "final_score": 0, "reason": "标题党"}
    if is_ad(title, snapshot):
        return {"review_status": "discarded", "final_score": 0, "reason": "广告/营销"}
    if has_rumor_marker(title + " " + snapshot):
        return {"review_status": "discarded", "final_score": 0, "reason": "疑似辟谣内容"}

    _, source_score = source_tier(url)
    time_score = time_score_from_str(collected_at, kind=kind) if collected_at else 100.0
    fact = fact if fact is not None else 5.0
    verify = (verify_need * 10) if verify_need is not None else VERIFY_DEFAULT
    # 简化交叉验证：verify_need>=5 时按中性 60 处理（真实搜索验证为预留接口）
    if verify_need is not None and verify_need >= 5:
        verify = VERIFY_DEFAULT
    final = compute_final(source_score, fact, verify, time_score)
    return {
        "source_score": source_score, "fact_score": fact, "verify_score": verify,
        "time_score": time_score, "final_score": final,
        "review_status": classify(final), "reason": "",
    }


def score_batch(items, session=None, api_key=None):
    """批量评分：规则层先行（0 Token 过滤），LLM 只评通过者。

    items: 列表，每项含 id/title/url/content_snapshot/collected_at。
    返回 {item_id: score_dict}。
    """
    llm_input, idx_map = [], {}
    out = {}
    for it in items:
        pre = score_item(it["title"], it.get("url", ""), it.get("content_snapshot", ""),
                         it.get("collected_at"), kind="tech_trend")
        if pre["review_status"] == "discarded":
            out[it["id"]] = pre
            continue
        idx_map[len(llm_input)] = it["id"]
        llm_input.append(it)
    if llm_input:
        llm_res = llm_score_batch(llm_input, session=session, api_key=api_key)
        for i, it in enumerate(llm_input):
            fact, verify_need = llm_res.get(i, (5, 3))
            out[it["id"]] = score_item(it["title"], it.get("url", ""),
                                       it.get("content_snapshot", ""), it.get("collected_at"),
                                       fact=fact, verify_need=verify_need)
    return out


# ---------------- CLI 兼容：JSON 行输入输出 ----------------

def score_items_json(items):
    """供 collect 集成：输入原始 items（含 id/title/url/content_snapshot/collected_at），
    返回评分结果 dict 便于调用方写库。"""
    return score_batch(items)
