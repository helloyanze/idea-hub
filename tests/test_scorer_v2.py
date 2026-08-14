"""Task S3.1: scorer 服务层 v2 测试（LLM 批量评分 + 降级 + 等权均值 + 两档分流）。"""

import json

import pytest

from idea_hub.collectors.base import RawItem
from idea_hub.services import scorer
from idea_hub.services.scorer import ScoredItem, score_items

DIMS = ["facts", "verification", "timeliness", "value"]


def _items(n=2):
    return [
        RawItem(title=f"T{i}", url=f"https://example.com/{i}",
                content_snapshot=f"snapshot {i}", source_id=1)
        for i in range(n)
    ]


def _fake_llm(rows):
    """构造返回 JSON 数组的 DeepSeek 假响应（chat/completions 形状）。"""
    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {
                "content": json.dumps(rows, ensure_ascii=False)}}]}

    def fake_post(url, **kwargs):
        return FakeResp()

    return fake_post


def test_no_key_all_admit():
    out = score_items(_items(2), api_key=None, dimensions=DIMS)
    assert len(out) == 2
    assert all(isinstance(s, ScoredItem) for s in out)
    assert all(s.verdict == "admit" for s in out)
    assert all(s.final_score is None for s in out)
    assert all(s.score_breakdown == {} for s in out)


def test_score_breakdown_shape(monkeypatch):
    rows = [
        {"title": "T0", "dimension_scores": {"facts": 8, "verification": 7,
                                             "timeliness": 9, "value": 8},
         "final_score": 8},
        {"title": "T1", "dimension_scores": {"facts": 6, "verification": 5,
                                             "timeliness": 7, "value": 6},
         "final_score": 6},
    ]
    monkeypatch.setattr(scorer.httpx, "post", _fake_llm(rows))
    out = score_items(_items(2), api_key="sk-test", dimensions=DIMS)
    assert out[0].score_breakdown == {"facts": 8, "verification": 7,
                                      "timeliness": 9, "value": 8}
    assert out[1].score_breakdown == {"facts": 6, "verification": 5,
                                      "timeliness": 7, "value": 6}


def test_verdict_by_threshold(monkeypatch):
    rows = [
        {"title": "T0", "dimension_scores": {"facts": 8, "verification": 8,
                                             "timeliness": 8, "value": 8}},
        {"title": "T1", "dimension_scores": {"facts": 7, "verification": 7,
                                             "timeliness": 7, "value": 7}},
    ]
    monkeypatch.setattr(scorer.httpx, "post", _fake_llm(rows))
    out = score_items(_items(2), api_key="sk-test", dimensions=DIMS,
                      threshold=8)
    assert out[0].verdict == "admit"    # 8 >= 8
    assert out[1].verdict == "discard"  # 7 < 8


def test_round_mean_half_up(monkeypatch):
    rows = [
        {"title": "T0", "dimension_scores": {"facts": 9, "verification": 8,
                                             "timeliness": 7, "value": 10}},
        {"title": "T1", "dimension_scores": {"facts": 8, "verification": 8,
                                             "timeliness": 8, "value": 7}},
    ]
    monkeypatch.setattr(scorer.httpx, "post", _fake_llm(rows))
    out = score_items(_items(2), api_key="sk-test", dimensions=DIMS)
    # (9+8+7+10)/4 = 8.5 → 四舍五入 9
    assert out[0].final_score == 9
    assert out[0].verdict == "admit"
    # (8+8+8+7)/4 = 7.75 → 四舍五入 8
    assert out[1].final_score == 8
    assert out[1].verdict == "admit"


def test_llm_error_falls_back_admit(monkeypatch):
    calls = {"n": 0}

    def boom(url, **kwargs):
        calls["n"] += 1
        raise RuntimeError("LLM down")

    monkeypatch.setattr(scorer.httpx, "post", boom)
    out = score_items(_items(2), api_key="sk-test", dimensions=DIMS)
    # 重试耗尽后降级：全量 admit、评分字段为空、收集不中断
    assert calls["n"] >= 1
    assert all(s.verdict == "admit" for s in out)
    assert all(s.final_score is None for s in out)
    assert all(s.score_breakdown == {} for s in out)


def test_rule_filter_discards_clickbait_ad(monkeypatch):
    """规则过滤层（0 token）：标题党/广告一票否决，且不进入 LLM 调用。"""
    seen_titles = []

    def fake_post(url, **kwargs):
        msg = kwargs["json"]["messages"][-1]["content"]
        seen_titles.append(msg)
        rows = [{"title": "正常标题",
                 "dimension_scores": {"facts": 8, "verification": 8,
                                      "timeliness": 8, "value": 8}}]
        return _fake_llm(rows)(url, **kwargs)

    monkeypatch.setattr(scorer.httpx, "post", fake_post)
    items = [
        RawItem(title="震惊！颠覆认知的操作", url="https://example.com/1",
                content_snapshot="", source_id=1),
        RawItem(title="加微信领取限时优惠", url="https://example.com/2",
                content_snapshot="", source_id=1),
        RawItem(title="正常标题", url="https://example.com/3",
                content_snapshot="", source_id=1),
    ]
    out = score_items(items, api_key="sk-test", dimensions=DIMS)
    assert out[0].verdict == "discard"  # 标题党
    assert out[1].verdict == "discard"  # 广告
    assert out[2].verdict == "admit"
    assert len(seen_titles) == 1  # LLM 只评通过规则的一条
    assert "震惊" not in seen_titles[0]
