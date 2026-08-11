import json
from idea_hub import db, collectors, models

class FakeResp:
    def __init__(self, payload): self._payload = payload
    def json(self): return self._payload
    def raise_for_status(self): pass

class FakeSession:
    def __init__(self, payload): self.payload = payload
    def get(self, url, timeout=None): return FakeResp(self.payload)

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
    collectors.fetch_hotlist = lambda url, items_path="data", session=None: orig(url, items_path=items_path, session=FakeSession(payload))
    try:
        res = collectors.collect_all(conn)
        assert res["collected"] == 2
        res2 = collectors.collect_all(conn)
        assert res2["collected"] == 0  # dedupe
    finally:
        collectors.fetch_hotlist = orig

def test_rss_items_shape(tmp_path):
    feed = tmp_path / "feed.xml"
    feed.write_text('<?xml version="1.0"?><rss version="2.0"><channel>'
                    '<item><title>R1</title><link>http://r1</link>'
                    '<description>desc1</description></item>'
                    '</channel></rss>', encoding="utf-8")
    items = collectors.fetch_rss(feed.as_uri())
    assert items[0]["title"] == "R1"
    assert items[0]["url"] == "http://r1"
    assert "desc1" in items[0]["content_snapshot"]
