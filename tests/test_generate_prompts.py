"""Task S4.2: DeepSeek 直调层 + 生成 prompts（services/llm.py + services/generate.py）。

Covers: chat_json 重试（先超时后成功）、宽松 JSON 解析（code fence / 前后文噪音）、
build_generate_prompt 结构（system/user 角色 + 候选 JSON 序列化）、
generate_one 输出校验（缺失字段补默认、非法 content_type 修正为 article、
tags 截断/去重）、无 api_key 抛错（生成不能降级——无 key 无法生成内容）。
"""
import json

import httpx
import pytest

from idea_hub.services import generate, llm

CANDIDATES = [
    {
        "hotspot_id": 1, "title": "热点A", "url": "https://example.com/a",
        "source_id": 1, "collected_at": "2026-08-14 10:00:00", "ttl_hours": 24,
        "final_score": 9, "score_breakdown": {"facts": 9},
    },
    {
        "hotspot_id": 2, "title": "热点B", "url": "https://example.com/b",
        "source_id": 2, "collected_at": "2026-08-14 09:00:00", "ttl_hours": None,
        "final_score": None, "score_breakdown": {},
    },
]


def _fake_post(content, fail_first=False):
    """httpx.post mock：返回给定 content 的 chat/completions 响应；可选首次抛超时。"""
    calls = {"n": 0}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        if fail_first and calls["n"] == 1:
            raise httpx.TimeoutException("timeout")
        return FakeResp()

    return fake_post, calls


# ---- llm.chat_json ----

def test_chat_json_returns_parsed_dict(monkeypatch):
    payload = {"title": "x", "content_type": "article"}
    fake_post, _ = _fake_post(json.dumps(payload, ensure_ascii=False))
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.chat_json([{"role": "user", "content": "hi"}], api_key="sk-test")
    assert out == payload


def test_chat_json_retries_on_timeout(monkeypatch):
    payload = {"ok": True}
    fake_post, calls = _fake_post(json.dumps(payload), fail_first=True)
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.chat_json([{"role": "user", "content": "hi"}], api_key="sk-test", max_retries=2)
    assert out == payload
    assert calls["n"] == 2  # 首次超时 + 重试成功


def test_chat_json_parse_strips_code_fence(monkeypatch):
    payload = {"title": "t", "tags": ["a"]}
    fenced = "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
    fake_post, _ = _fake_post(fenced)
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.chat_json([{"role": "user", "content": "hi"}], api_key="sk-test")
    assert out == payload


def test_chat_json_tolerant_parse_with_surrounding_text(monkeypatch):
    payload = {"title": "t", "tags": ["a"]}
    messy = "好的，结果如下：\n" + json.dumps(payload, ensure_ascii=False) + "\n希望能帮到你。"
    fake_post, _ = _fake_post(messy)
    monkeypatch.setattr(llm.httpx, "post", fake_post)
    out = llm.chat_json([{"role": "user", "content": "hi"}], api_key="sk-test")
    assert out == payload


# ---- build_generate_prompt ----

def test_build_generate_prompt_structure():
    messages = generate.build_generate_prompt(CANDIDATES)
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "content" in messages[0] and "content" in messages[1]


def test_build_generate_prompt_serializes_candidates():
    messages = generate.build_generate_prompt(CANDIDATES)
    user_content = messages[1]["content"]
    # 候选 JSON 序列化进 user 内容
    assert "热点A" in user_content
    assert "https://example.com/a" in user_content
    assert "热点B" in user_content
    # system 人设声明输出字段，且不含评分（评分继承热点，spec 5.4 评分数据链）
    for field in ("title", "idea_summary", "full_idea", "content_type", "tags"):
        assert field in messages[0]["content"]
    assert "score" not in messages[0]["content"]


# ---- generate_one ----

def test_generate_one_returns_normalized_list(monkeypatch):
    raw = [
        {"title": "构思1", "idea_summary": "摘要1", "full_idea": "# 正文\n内容",
         "content_type": "article", "tags": ["AI", "科技"]},
        {"title": "构思2", "idea_summary": "摘要2", "full_idea": "正文2",
         "content_type": "video_script", "tags": ["视频", "教程", "视频"]},
    ]
    monkeypatch.setattr(generate, "chat_json", lambda *a, **k: raw)
    out = generate.generate_one(CANDIDATES, api_key="sk-test")
    assert len(out) == 2
    assert out[0] == {"title": "构思1", "idea_summary": "摘要1", "full_idea": "# 正文\n内容",
                      "content_type": "article", "tags": ["AI", "科技"]}
    assert out[1]["content_type"] == "video_script"
    assert out[1]["tags"] == ["视频", "教程"]  # 去重


def test_generate_one_fills_missing_fields(monkeypatch):
    raw = [{"title": "只有标题"}]
    monkeypatch.setattr(generate, "chat_json", lambda *a, **k: raw)
    out = generate.generate_one(CANDIDATES, api_key="sk-test")
    assert out[0]["idea_summary"] == ""
    assert out[0]["full_idea"] == ""
    assert out[0]["content_type"] == "article"  # 缺失 → article
    assert out[0]["tags"] == []


def test_generate_one_fixes_invalid_content_type(monkeypatch):
    raw = [{"title": "x", "content_type": "blog_post", "tags": ["a"]}]
    monkeypatch.setattr(generate, "chat_json", lambda *a, **k: raw)
    out = generate.generate_one(CANDIDATES, api_key="sk-test")
    assert out[0]["content_type"] == "article"


def test_generate_one_cleans_tags(monkeypatch):
    raw = [{"title": "x", "tags": ["a", "a", "b", "c", "d", "e", "f", 123, "", "  "]}]
    monkeypatch.setattr(generate, "chat_json", lambda *a, **k: raw)
    out = generate.generate_one(CANDIDATES, api_key="sk-test")
    assert out[0]["tags"] == ["a", "b", "c", "d", "e"]  # 去重 + 截断 5 + 丢弃非字符串


def test_generate_one_drops_non_dict_items(monkeypatch):
    raw = [{"title": "ok", "tags": ["x"]}, "garbage", None]
    monkeypatch.setattr(generate, "chat_json", lambda *a, **k: raw)
    out = generate.generate_one(CANDIDATES, api_key="sk-test")
    assert len(out) == 1
    assert out[0]["title"] == "ok"


def test_generate_one_requires_api_key():
    # 生成不能降级（对比评分降级策略）：无 key 直接抛错，由端点层转 400
    with pytest.raises(ValueError):
        generate.generate_one(CANDIDATES, api_key=None)
    with pytest.raises(ValueError):
        generate.generate_one(CANDIDATES, api_key="")
