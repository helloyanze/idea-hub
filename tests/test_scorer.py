"""评分机制（scorer.py）单元测试：规则层 0 Token + 聚合阈值。"""
import pytest
from idea_hub import scorer


def test_source_tier_lookup():
    assert scorer.source_tier("https://www.gov.cn/x")[0] == "S"
    assert scorer.source_tier("https://36kr.com/p/1")[0] == "A"
    assert scorer.source_tier("https://github.com/foo/bar")[0] == "B"
    assert scorer.source_tier("https://weibo.com/u/1")[0] == "C"
    assert scorer.source_tier("https://unknown-site.example.com/x") == ("D", 35)


def test_clickbait_and_ad_detection():
    assert scorer.is_clickbait("震惊！某公司颠覆认知的操作") is True      # 2 词命中
    assert scorer.is_clickbait("普通新闻标题") is False
    assert scorer.is_ad("加微信领取限时优惠") is True
    assert scorer.is_ad("正常的技术文章") is False


def test_time_score_decay():
    assert scorer.time_score_from_hours(0) == 100.0
    assert scorer.time_score_from_hours(24, "tech_trend") < 100.0
    assert scorer.time_score_from_hours(24, "breaking_news") < scorer.time_score_from_hours(24, "tech_trend")
    assert scorer.time_score_from_hours(24, "policy_data") > scorer.time_score_from_hours(24, "tech_trend")


def test_compute_final_formula():
    # 0.35*80 + 0.25*(7*10) + 0.25*80 + 0.15*90 = 28 + 17.5 + 20 + 13.5 = 79.0
    f = scorer.compute_final(source_score=80, fact_score=7, verify_score=80, time_score=90)
    assert f == 79.0


def test_classify_thresholds():
    assert scorer.classify(80) == "collected"
    assert scorer.classify(75) == "collected"
    assert scorer.classify(60) == "review"
    assert scorer.classify(40) == "discarded"


def test_score_item_veto_rules():
    # 标题党一票否决
    r = scorer.score_item("震惊！史上最炸裂的消息", "https://github.com/x", fact=None)
    assert r["review_status"] == "discarded" and "标题党" in r["reason"]
    # 黑名单一票否决
    scorer.BLOCKLIST.append("fake-news.test")
    try:
        r = scorer.score_item("正常标题", "https://fake-news.test/x")
        assert r["review_status"] == "discarded" and "黑名单" in r["reason"]
    finally:
        scorer.BLOCKLIST.clear()


def test_score_item_aggregate_high_quality():
    """高可信来源 + 高事实性 → 收录。"""
    r = scorer.score_item("统计局发布 7 月 CPI 数据", "https://www.gov.cn/x",
                          fact=9, verify_need=2, collected_at=None)
    assert r["review_status"] == "collected"
    assert r["source_score"] == 95
    assert r["fact_score"] == 9
    assert r["final_score"] > 75


def test_score_item_low_quality_discarded():
    """低可信来源 + 低事实性 → 丢弃。"""
    r = scorer.score_item("某明星的离谱传闻", "https://weibo.com/u/1",
                          fact=2, verify_need=8, collected_at=None)
    assert r["review_status"] == "discarded"
    assert r["source_score"] == 50


def test_score_batch_llm_skip_when_missing_key(monkeypatch):
    """无 API key 时 score_batch 的 LLM 调用应报错（由 collect 降级处理）。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(scorer.pathlib.Path, "read_text", lambda self, **kw: (_ for _ in ()).throw(OSError()))
    with pytest.raises(RuntimeError):
        scorer.llm_score_batch([{"title": "x", "summary": "y"}])
