import json, threading, http.server
import pytest
from idea_hub import db, collectors, models

class FakeResp:
    def __init__(self, payload): self._payload = payload
    def json(self): return self._payload
    def raise_for_status(self): pass

class FakeSession:
    def __init__(self, payload): self.payload = payload
    def get(self, url, timeout=None): return FakeResp(self.payload)

class RSSHandler(http.server.BaseHTTPRequestHandler):
    feed = b""
    def do_GET(self):
        body = self.feed
        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

@pytest.fixture
def rss_url(tmp_path):
    """本地 HTTP 服务器提供 RSS 内容（fetch_rss 改走 requests 后不支持 file://）。"""
    feed = tmp_path / "feed.xml"
    feed.write_text('<?xml version="1.0"?><rss version="2.0"><channel>'
                    '<item><title>R1</title><link>http://r1</link>'
                    '<description>desc1</description></item>'
                    '<item><title>R2</title><link>http://r2</link>'
                    '<description>desc2</description></item>'
                    '</channel></rss>', encoding="utf-8")
    RSSHandler.feed = feed.read_bytes()
    srv = http.server.HTTPServer(("127.0.0.1", 0), RSSHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/feed.xml"
    finally:
        srv.shutdown()

def test_fetch_hotlist_nested_path():
    payload = {"result": {"list": [{"title": "A", "url": "http://a", "hot": 99}]}}
    items = collectors.fetch_hotlist("http://x", items_path="result.list", session=FakeSession(payload))
    assert items[0]["title"] == "A"
    assert items[0]["content_snapshot"] == "热度:99"

def test_collect_all_dedupes(conn, tmp_path):
    sid = models.create_source(conn, type="hotlist", name="测试榜",
                               url=f"file://{tmp_path}/nope.json")
    payload = {"data": [{"title": "T1", "url": "http://u1"},
                        {"title": "T2", "url": "http://u2"}]}
    # monkeypatch: use fake session for any url
    orig = collectors.fetch_hotlist
    collectors.fetch_hotlist = lambda url, items_path="data", title_field="title", session=None: orig(url, items_path=items_path, title_field=title_field, session=FakeSession(payload))
    try:
        res = collectors.collect_all(conn)
        assert res["collected"] == 2
        res2 = collectors.collect_all(conn)
        assert res2["collected"] == 0  # dedupe
    finally:
        collectors.fetch_hotlist = orig

def test_rss_items_shape(rss_url):
    items = collectors.fetch_rss(rss_url)
    assert items[0]["title"] == "R1"
    assert items[0]["url"] == "http://r1"
    assert "desc1" in items[0]["content_snapshot"]

def test_collect_all_rss(conn, rss_url):
    sid = models.create_source(conn, type="rss", name="RSS源", url=rss_url)
    res = collectors.collect_all(conn)
    assert res["errors"] == []
    assert res["collected"] == 2
    rows = conn.execute("SELECT title, url FROM hot_items WHERE source_id=?", (sid,)).fetchall()
    assert len(rows) == 2
    assert {r["title"] for r in rows} == {"R1", "R2"}

def test_collect_all_error_isolation(conn, rss_url):
    bad = models.create_source(conn, type="hotlist", name="坏源",
                               url="http://127.0.0.1:1/nope")
    good = models.create_source(conn, type="rss", name="好源", url=rss_url)
    res = collectors.collect_all(conn)
    assert len(res["errors"]) == 1
    assert "坏源" in res["errors"][0]
    assert res["collected"] == 2
    rows = conn.execute("SELECT title, url FROM hot_items WHERE source_id=?", (good,)).fetchall()
    assert {r["title"] for r in rows} == {"R1", "R2"}

def test_fetch_hotlist_custom_title_field(tmp_path):
    """自定义 title_field：适配不同热榜 API 的字段命名。"""
    payload = {"result": {"list": [{"word": "热搜词", "url": "http://w", "hot": 55}]}}
    items = collectors.fetch_hotlist("http://x", items_path="result.list",
                                     title_field="word", session=FakeSession(payload))
    assert items[0]["title"] == "热搜词"
    assert items[0]["content_snapshot"] == "热度:55"

def test_collect_all_uses_source_config(conn, tmp_path):
    """collect_all 使用来源配置的 items_path/title_field。"""
    sid = models.create_source(conn, type="hotlist", name="百度热榜",
                               url="http://x", items_path="data.cards.0.content.0.content",
                               title_field="word")
    payload = {"data": {"cards": [{"content": [{"content": [
        {"word": "热词A", "url": "http://a", "hot": 9},
        {"word": "热词B", "url": "http://b"},
    ]}]}]}}
    orig = collectors.fetch_hotlist
    collectors.fetch_hotlist = lambda url, items_path="data", title_field="title", session=None: \
        orig(url, items_path=items_path, title_field=title_field, session=FakeSession(payload))
    try:
        res = collectors.collect_all(conn)
        assert res["collected"] == 2
        assert res["errors"] == []
    finally:
        collectors.fetch_hotlist = orig

def test_migration_adds_columns(tmp_path):
    """老库迁移：缺少 items_path/title_field 列时自动补充。"""
    import sqlite3 as _sqlite3
    db_path = tmp_path / "old.db"
    c = _sqlite3.connect(str(db_path))
    c.execute("CREATE TABLE sources (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "type TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL, "
              "enabled INTEGER NOT NULL DEFAULT 1)")
    c.execute("INSERT INTO sources (type, name, url) VALUES ('rss','旧源','http://x')")
    c.commit(); c.close()
    conn = db.connect(str(db_path))
    db.init_schema(conn)
    row = conn.execute("SELECT * FROM sources").fetchone()
    assert row["items_path"] == "data" and row["title_field"] == "title"
    conn.close()

# ---- 新爬虫 + 关键词过滤测试 ----

def test_matches_keywords():
    item = {"title": "LangChain 新版本发布", "url": "http://x", "content_snapshot": "agent 工具"}
    assert collectors._matches_keywords(item, "langchain, agent")
    # 关键词是子串匹配，注意误匹配（如 langchAI n 含 "ai"）——用无子串冲突的词验证
    assert collectors._matches_keywords(item, "zzzz") is False
    assert collectors._matches_keywords(item, "") is True    # 空=不过滤

def test_fetch_github_trending_parse():
    html = """<html><body>
      <article class="Box-row">
        <h2><a href="/langchain-ai/langchain">langchain-ai / langchain</a></h2>
        <p>构建 LLM 应用的框架</p>
        <span class="d-inline-block float-sm-right">1,234 stars today</span>
      </article>
      <article class="Box-row">
        <h2><a href="/foo/bar">foo / bar</a></h2>
        <p>另一个项目</p>
      </article>
    </body></html>"""
    class FakeResp:
        text = html
        def raise_for_status(self): pass
    class FakeSession:
        def get(self, url, timeout=None, headers=None): return FakeResp()
    items = collectors.fetch_github_trending(session=FakeSession())
    assert items[0]["title"] == "langchain-ai/langchain"
    assert items[0]["url"] == "https://github.com/langchain-ai/langchain"
    assert "1,234 stars today" in items[0]["content_snapshot"]
    assert len(items) == 2

def test_fetch_hackernews_shape():
    import json
    class FakeSession:
        def __init__(self):
            self.calls = []
        def get(self, url, timeout=None):
            self.calls.append(url)
            if url.endswith("topstories.json"):
                return _JsonResp([1, 2])
            data = {"id": int(url.split("/item/")[1].split(".")[0]),
                    "type": "story", "title": f"HN 故事 {url[-5]}",
                    "url": "http://ext", "score": 100, "descendants": 20}
            return _JsonResp(data)
    class _JsonResp:
        def __init__(self, obj): self._obj = obj
        def raise_for_status(self): pass
        @property
        def text(self): return json.dumps(self._obj)
    items = collectors.fetch_hackernews(limit=2, session=FakeSession())
    assert len(items) == 2
    assert items[0]["title"].startswith("HN 故事")
    assert "得分:100" in items[0]["content_snapshot"]

def test_collect_all_keywords_filter(conn, tmp_path):
    """关键词白名单过滤生效。"""
    sid = models.create_source(conn, type="hotlist", name="过滤源", url="http://x",
                               keywords="ai, agent")
    payload = {"data": [
        {"title": "AI 编程助手发布", "url": "http://a"},
        {"title": "某明星绯闻", "url": "http://b"},
    ]}
    orig = collectors.fetch_hotlist
    collectors.fetch_hotlist = lambda url, items_path="data", title_field="title", session=None: \
        orig(url, items_path=items_path, title_field=title_field, session=FakeSession(payload))
    try:
        res = collectors.collect_all(conn)
        assert res["collected"] == 1
        rows = conn.execute("SELECT title FROM hot_items").fetchall()
        assert rows[0]["title"] == "AI 编程助手发布"
    finally:
        collectors.fetch_hotlist = orig

def test_collect_all_new_types_dispatch(conn):
    """github-trending / hackernews 类型分发。"""
    sid1 = models.create_source(conn, type="github-trending", name="GT", url="")
    sid2 = models.create_source(conn, type="hackernews", name="HN", url="")
    orig_gt, orig_hn = collectors.fetch_github_trending, collectors.fetch_hackernews
    collectors.fetch_github_trending = lambda url="", session=None: [
        {"title": "repo/x", "url": "http://g", "content_snapshot": "stars"}]
    collectors.fetch_hackernews = lambda limit=30, session=None: [
        {"title": "HN 热帖", "url": "http://h", "content_snapshot": "得分:1"}]
    try:
        res = collectors.collect_all(conn)
        assert res["collected"] == 2
        assert res["errors"] == []
    finally:
        collectors.fetch_github_trending, collectors.fetch_hackernews = orig_gt, orig_hn
