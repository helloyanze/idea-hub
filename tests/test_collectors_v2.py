"""Task S2.1: collectors 包（base 抽象 + hotlist/rss/github/hn 四渠道 + orchestrator + registry）。

样例数据基于真实 API 结构：
- 百度热榜: https://top.baidu.com/api/board?platform=wise&tab=realtime
  → data.cards[0].content[0].content 为榜单条目列表（条目含 word/url）
- RSS: feedparser 解析标准 RSS 2.0 XML
- GitHub Trending: https://github.com/trending → article.Box-row 卡片（h2 a / p / span.d-inline-block.float-sm-right）
- HN: hacker-news.firebaseio.com/v0/topstories.json + item/{id}.json

所有 HTTP 请求经 monkeypatch requests.get 注入假响应，不触网。
"""
import pytest

from idea_hub.collectors import collect_all, collector_registry
from idea_hub.collectors.base import BaseCollector, RawItem
from idea_hub.collectors.github import GithubTrendingCollector
from idea_hub.collectors.hackernews import HackerNewsCollector
from idea_hub.collectors.hotlist import HotlistCollector
from idea_hub.collectors.rss import RssCollector

# ---------- 真实 API 结构样例 ----------

BAIDU_SAMPLE = {
    "success": True,
    "data": {
        "cards": [
            {
                "component": "tabTextList",
                "content": [
                    {
                        "content": [
                            {"isTop": True, "url": "https://m.baidu.com/s?word=A", "word": "城市不仅要有高度 更要有温度"},
                            {"isTop": False, "index": 1, "url": "https://m.baidu.com/s?word=B", "word": "油价将迎来年内第五次下调"},
                        ]
                    }
                ],
            }
        ]
    },
}

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Example Feed</title>
<link>http://example.com/</link>
<item>
<title>First Post</title>
<link>http://example.com/first</link>
<description>First summary text</description>
</item>
<item>
<title>Second Post</title>
<link>http://example.com/second</link>
<description>Second summary text</description>
</item>
</channel>
</rss>"""

GITHUB_SAMPLE = """
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/octocat/hello-world"><span>octocat</span> / <span>hello-world</span></a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">A sample repository for demonstration.</p>
  <div class="f6 color-fg-muted mt-2">
    <span class="d-inline-block float-sm-right"><svg></svg> 1,234 stars today</span>
  </div>
</article>
<article class="Box-row">
  <h2 class="h3 lh-condensed"><a href="/torvalds/linux"><span>torvalds</span> / <span>linux</span></a></h2>
  <p class="col-9 color-fg-muted my-1 pr-4">Linux kernel source tree.</p>
  <div class="f6 color-fg-muted mt-2">
    <span class="d-inline-block float-sm-right"><svg></svg> 567 stars today</span>
  </div>
</article>
"""

HN_TOP_IDS = [111, 222, 333]
HN_ITEMS = {
    111: {"id": 111, "type": "story", "title": "Show HN: A Tiny Thing",
          "url": "https://example.com/tiny", "score": 456, "descendants": 89},
    222: {"id": 222, "type": "story", "title": "Story Without URL", "score": 12, "descendants": 3},
    333: {"id": 333, "type": "comment", "text": "not a story"},
}


class FakeResponse:
    def __init__(self, payload=None, text="", content=b""):
        self._payload = payload
        self.text = text
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _src(src_id, src_type, url, **overrides):
    row = {"id": src_id, "type": src_type, "name": "test", "url": url, "enabled": 1,
           "items_path": "data", "title_field": "title", "keywords": "",
           "ttl_hours": 24, "channel_config": "{}"}
    row.update(overrides)
    return row


# ---------- 四渠道解析 ----------

def test_hotlist_parse(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get",
                        lambda url, timeout=15: FakeResponse(payload=BAIDU_SAMPLE))
    src = _src(7, "hotlist", "https://top.baidu.com/api/board?platform=wise&tab=realtime",
               items_path="data.cards.0.content.0.content", title_field="word")
    items = HotlistCollector(src).fetch()
    assert len(items) == 2
    assert isinstance(items[0], RawItem)
    assert items[0].title == "城市不仅要有高度 更要有温度"
    assert items[0].url == "https://m.baidu.com/s?word=A"
    assert items[1].title == "油价将迎来年内第五次下调"
    assert all(i.source_id == 7 for i in items)


def test_rss_parse(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get",
                        lambda url, timeout=15: FakeResponse(content=RSS_SAMPLE.encode("utf-8")))
    src = _src(3, "rss", "http://example.com/feed.xml")
    items = RssCollector(src).fetch()
    assert len(items) == 2
    assert items[0].title == "First Post"
    assert items[0].url == "http://example.com/first"
    assert items[0].content_snapshot == "First summary text"
    assert all(i.source_id == 3 for i in items)


def test_github_trending_parse(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get",
                        lambda url, timeout=15, headers=None: FakeResponse(text=GITHUB_SAMPLE))
    src = _src(5, "github-trending", "https://github.com/trending?since=daily")
    items = GithubTrendingCollector(src).fetch()
    assert len(items) == 2
    assert items[0].title == "octocat/hello-world"
    assert items[0].url == "https://github.com/octocat/hello-world"
    assert "1,234 stars today" in items[0].content_snapshot
    assert "A sample repository for demonstration." in items[0].content_snapshot
    assert items[1].title == "torvalds/linux"
    assert all(i.source_id == 5 for i in items)


def test_hn_parse(monkeypatch):
    import requests

    def _hn_get(url, timeout=15):
        if url.endswith("/topstories.json"):
            return FakeResponse(payload=HN_TOP_IDS)
        hid = url.rstrip(".json").rsplit("/", 1)[-1]
        return FakeResponse(payload=HN_ITEMS[int(hid)])

    monkeypatch.setattr(requests, "get", _hn_get)
    src = _src(9, "hackernews", "https://hacker-news.firebaseio.com/v0/topstories.json")
    items = HackerNewsCollector(src).fetch()
    assert len(items) == 2  # 111 有 url；222 无 url 回退 HN 链接；333 非 story 跳过
    assert items[0].title == "Show HN: A Tiny Thing"
    assert items[0].url == "https://example.com/tiny"
    assert "得分:456" in items[0].content_snapshot
    assert items[1].url == "https://news.ycombinator.com/item?id=222"
    assert all(i.source_id == 9 for i in items)


# ---------- registry ----------

def test_collector_registry_maps_all_source_types():
    assert set(collector_registry) == {"hotlist", "rss", "github-trending", "hackernews"}
    for cls in collector_registry.values():
        assert issubclass(cls, BaseCollector)


# ---------- orchestrator ----------

def test_orchestrator_skips_disabled_and_continues_on_error(conn, monkeypatch):
    import requests
    conn.execute("INSERT INTO sources (type, name, url, enabled) VALUES ('rss', '好源', 'http://ok.example/feed', 1)")
    ok_id = conn.execute("SELECT id FROM sources WHERE name='好源'").fetchone()[0]
    conn.execute("INSERT INTO sources (type, name, url, enabled) VALUES ('hotlist', '坏源', 'http://fail.example/api', 1)")
    bad_id = conn.execute("SELECT id FROM sources WHERE name='坏源'").fetchone()[0]
    conn.execute("INSERT INTO sources (type, name, url, enabled) VALUES ('hotlist', '禁用源', 'http://off.example/api', 0)")
    conn.commit()

    def fake_get(url, timeout=15, headers=None):
        if url == "http://ok.example/feed":
            return FakeResponse(content=RSS_SAMPLE.encode("utf-8"))
        if url == "http://fail.example/api":
            raise RuntimeError("boom: connection refused")
        raise AssertionError(f"disabled source should not be fetched: {url}")

    monkeypatch.setattr(requests, "get", fake_get)
    result = collect_all(conn)
    assert "First Post" in [it.title for it in result["items"]]
    assert all(it.source_id == ok_id for it in result["items"])
    assert result["errors"] == [{"source_id": bad_id, "error": "boom: connection refused"}]


def test_orchestrator_limit_per_source(conn, monkeypatch):
    import requests
    conn.execute(
        "INSERT INTO sources (type, name, url, enabled, items_path, title_field) "
        "VALUES ('hotlist', '百度热榜', 'http://baidu.example/api', 1, "
        "'data.cards.0.content.0.content', 'word')")
    conn.commit()
    monkeypatch.setattr(requests, "get",
                        lambda url, timeout=15: FakeResponse(payload=BAIDU_SAMPLE))
    result = collect_all(conn, limit_per_source=1)
    assert len(result["items"]) == 1
    assert result["items"][0].title == "城市不仅要有高度 更要有温度"
    assert result["errors"] == []
