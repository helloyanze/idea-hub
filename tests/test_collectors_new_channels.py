"""Task S2.2: 新渠道（zhihu-hotlist / weibo-hotlist / v2ex）collector + registry + 解析失败。

样例数据基于真实 API 结构：
- 知乎热榜: https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50
  → data[].target.title / target.url（detail_text 为热度文本）
- 微博热搜: https://weibo.com/ajax/side/hotSearch
  → data.realtime[].word / word_scheme（num 为热度值）
- V2EX 热帖: https://www.v2ex.com/api/topics/hot.json
  → 顶层数组，title / url / replies

所有 HTTP 请求经 monkeypatch httpx.get 注入假响应，不触网。
"""
import pytest

from idea_hub.collectors import collect_all, collector_registry
from idea_hub.collectors.base import BaseCollector, CollectorError, RawItem
from idea_hub.collectors.zhihu import ZhihuCollector
from idea_hub.collectors.weibo import WeiboCollector
from idea_hub.collectors.v2ex import V2exCollector

# ---------- 真实 API 结构样例 ----------

ZHIHU_SAMPLE = {
    "data": [
        {
            "type": "hot_list_feed",
            "target": {
                "id": "601234567",
                "type": "question",
                "title": "如何看待某地天气持续高温？",
                "url": "https://www.zhihu.com/question/601234567",
            },
            "detail_text": "1234 万热度",
        },
        {
            "type": "hot_list_feed",
            "target": {
                "id": "609876543",
                "type": "question",
                "title": "如何评价今年暑期的电影市场？",
                "url": "https://www.zhihu.com/question/609876543",
            },
            "detail_text": "987 万热度",
        },
    ],
    "paging": {"is_end": True, "next": None},
}

WEIBO_SAMPLE = {
    "ok": 1,
    "data": {
        "realtime": [
            {
                "word": "微博热搜第一",
                "word_scheme": "https://s.weibo.com/weibo?q=%23%E5%BE%AE%E5%8D%9A%E7%83%AD%E6%90%9C%E7%AC%AC%E4%B8%80%23",
                "num": 1234567,
                "label_name": "热",
            },
            {
                "word": "第二条热搜",
                "word_scheme": "https://s.weibo.com/weibo?q=%23%E7%AC%AC%E4%BA%8C%E6%9D%A1%E7%83%AD%E6%90%9C%23",
                "num": 987654,
            },
        ],
        "hotgov": {},
    },
}

V2EX_SAMPLE = [
    {
        "id": 12345,
        "title": "V2EX 今日热帖",
        "url": "https://www.v2ex.com/t/12345",
        "content": "正文内容摘要",
        "replies": 42,
        "node": {"title": "程序员", "name": "programmer"},
    },
    {
        "id": 67890,
        "title": "分享一个命令行工具",
        "url": "https://www.v2ex.com/t/67890",
        "content": "工具介绍",
        "replies": 15,
        "node": {"title": "分享创造", "name": "create"},
    },
]


class FakeHttpxResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


def _src(src_id, src_type, url, **overrides):
    row = {"id": src_id, "type": src_type, "name": "test", "url": url, "enabled": 1,
           "items_path": "data", "title_field": "title", "keywords": "",
           "ttl_hours": 24, "channel_config": "{}"}
    row.update(overrides)
    return row


# ---------- 三渠道解析 ----------

def test_zhihu_parse(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda url, **kwargs: FakeHttpxResponse(payload=ZHIHU_SAMPLE))
    src = _src(10, "zhihu-hotlist",
               "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50")
    items = ZhihuCollector(src).fetch()
    assert len(items) == 2
    assert isinstance(items[0], RawItem)
    assert items[0].title == "如何看待某地天气持续高温？"
    assert items[0].url == "https://www.zhihu.com/question/601234567"
    assert "1234 万热度" in items[0].content_snapshot
    assert items[1].title == "如何评价今年暑期的电影市场？"
    assert all(i.source_id == 10 for i in items)


def test_weibo_parse(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda url, **kwargs: FakeHttpxResponse(payload=WEIBO_SAMPLE))
    src = _src(11, "weibo-hotlist", "https://weibo.com/ajax/side/hotSearch")
    items = WeiboCollector(src).fetch()
    assert len(items) == 2
    assert items[0].title == "微博热搜第一"
    assert items[0].url.startswith("https://s.weibo.com/weibo?q=")
    assert "1234567" in items[0].content_snapshot
    assert items[1].title == "第二条热搜"
    assert all(i.source_id == 11 for i in items)


def test_v2ex_parse(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda url, **kwargs: FakeHttpxResponse(payload=V2EX_SAMPLE))
    src = _src(12, "v2ex", "https://www.v2ex.com/api/topics/hot.json")
    items = V2exCollector(src).fetch()
    assert len(items) == 2
    assert items[0].title == "V2EX 今日热帖"
    assert items[0].url == "https://www.v2ex.com/t/12345"
    assert "42" in items[0].content_snapshot
    assert items[1].title == "分享一个命令行工具"
    assert all(i.source_id == 12 for i in items)


# ---------- channel_config：limit 覆盖 ----------

def test_zhihu_channel_config_limit(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda url, **kwargs: FakeHttpxResponse(payload=ZHIHU_SAMPLE))
    src = _src(10, "zhihu-hotlist", "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50",
               channel_config='{"limit": 1}')
    items = ZhihuCollector(src).fetch()
    assert len(items) == 1
    assert items[0].title == "如何看待某地天气持续高温？"


# ---------- 解析失败抛 CollectorError ----------

@pytest.mark.parametrize("collector_cls,payload", [
    (ZhihuCollector, {"foo": "bar"}),          # 缺 data 键
    (WeiboCollector, {"ok": 0, "data": {}}),   # 缺 data.realtime
    (V2exCollector, {"not_a_list": True}),     # 不是数组
])
def test_parse_failure_raises_collector_error(monkeypatch, collector_cls, payload):
    import httpx
    monkeypatch.setattr(httpx, "get",
                        lambda url, **kwargs: FakeHttpxResponse(payload=payload))
    src = _src(13, collector_cls.type, "http://example.invalid/api")
    with pytest.raises(CollectorError):
        collector_cls(src).fetch()


# ---------- registry 注册 ----------

def test_registry_includes_new_channels():
    for src_type in ("zhihu-hotlist", "weibo-hotlist", "v2ex"):
        assert src_type in collector_registry
        assert issubclass(collector_registry[src_type], BaseCollector)


# ---------- orchestrator 集成（新 type 入库 + 收集） ----------

def test_orchestrator_collects_zhihu_source(conn, monkeypatch):
    import httpx
    conn.execute(
        "INSERT INTO sources (type, name, url, enabled) "
        "VALUES ('zhihu-hotlist', '知乎热榜', "
        "'https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50', 1)")
    conn.commit()
    monkeypatch.setattr(httpx, "get",
                        lambda url, **kwargs: FakeHttpxResponse(payload=ZHIHU_SAMPLE))
    result = collect_all(conn)
    assert result["errors"] == []
    assert [it.title for it in result["items"]] == ["如何看待某地天气持续高温？",
                                                    "如何评价今年暑期的电影市场？"]
    assert result["items"][0].source_id == conn.execute(
        "SELECT id FROM sources WHERE type='zhihu-hotlist'").fetchone()[0]


# ---------- channel_config.headers：自定义请求头（cookie，解决 401/403） ----------

def test_zhihu_custom_headers_cookie(monkeypatch):
    import httpx
    captured = {}
    def fake_get(url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return FakeHttpxResponse(payload=ZHIHU_SAMPLE)
    monkeypatch.setattr(httpx, "get", fake_get)
    src = _src(10, "zhihu-hotlist",
               "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50",
               channel_config='{"headers": {"Cookie": "z_c0=abc"}}')
    items = ZhihuCollector(src).fetch()
    assert len(items) == 2
    assert captured["headers"]["Cookie"] == "z_c0=abc"
    assert "User-Agent" in captured["headers"]


def test_weibo_custom_headers_cookie(monkeypatch):
    import httpx
    captured = {}
    def fake_get(url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return FakeHttpxResponse(payload=WEIBO_SAMPLE)
    monkeypatch.setattr(httpx, "get", fake_get)
    src = _src(11, "weibo-hotlist", "https://weibo.com/ajax/side/hotSearch",
               channel_config='{"headers": {"Cookie": "SUB=xyz"}}')
    items = WeiboCollector(src).fetch()
    assert len(items) == 2
    assert captured["headers"]["Cookie"] == "SUB=xyz"
