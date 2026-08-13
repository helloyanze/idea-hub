"""Task S2.3: services/filtering.py — 关键词白名单过滤 + URL 去重 + 快照截断。

spec 5.4：关键词过滤（标题 OR 语义）、URL 去重（DB 已有 + 同批重复）、快照 2000 字符截断。
"""
from idea_hub.collectors.base import RawItem
from idea_hub.services import filtering


def _item(title, url="https://example.com/x", snapshot="", source_id=1):
    return RawItem(title=title, url=url, content_snapshot=snapshot, source_id=source_id)


def _add_source(conn, name):
    conn.execute(
        "INSERT INTO sources (type, name, url, enabled) VALUES ('rss', ?, 'http://example.com/feed', 1)",
        (name,),
    )
    conn.commit()
    return conn.execute("SELECT id FROM sources WHERE name = ?", (name,)).fetchone()[0]


# ---------- 关键词白名单（spec 5.4） ----------

def test_keywords_filter_or_semantics():
    """关键词逗号分隔，标题包含任一关键词即保留（OR 语义）。"""
    items = [
        _item("AI 大模型落地实践"),
        _item("科技公司融资动态"),
        _item("美食探店分享"),
        _item("AI与科技融合趋势"),
    ]
    kept = filtering.apply_keywords_filter(items, "AI,科技")
    assert [i.title for i in kept] == ["AI 大模型落地实践", "科技公司融资动态", "AI与科技融合趋势"]


def test_keywords_filter_strips_whitespace_tokens():
    items = [_item("AI 大模型落地实践"), _item("美食探店分享")]
    kept = filtering.apply_keywords_filter(items, " AI ,  ")
    assert [i.title for i in kept] == ["AI 大模型落地实践"]


def test_empty_keywords_no_filter():
    """空字符串 = 不过滤，原样返回全部。"""
    items = [_item("任意标题一"), _item("任意标题二")]
    assert filtering.apply_keywords_filter(items, "") == items


# ---------- URL 去重（spec 5.1） ----------

def test_dedup_against_db_and_batch(conn):
    """库中已存在的 URL 与同批重复的 URL 均跳过，顺序保持。"""
    sid1 = _add_source(conn, "源一")
    sid2 = _add_source(conn, "源二")
    conn.execute(
        "INSERT INTO hot_items (source_id, title, url) VALUES (?, '库中已有', 'https://example.com/old')",
        (sid1,),
    )
    conn.commit()
    items = [
        _item("库中已有", "https://example.com/old", source_id=sid1),
        _item("同批重复A", "https://example.com/dup", source_id=sid2),
        _item("同批重复B", "https://example.com/dup", source_id=sid2),
        _item("新条目", "https://example.com/new", source_id=sid1),
    ]
    kept = filtering.dedup_by_url(conn, items)
    assert [i.title for i in kept] == ["同批重复A", "新条目"]


def test_dedup_keeps_empty_url_items(conn):
    """url 为空字符串的条目不参与去重（空串不是 URL，避免误删无 URL 条目）。"""
    sid = _add_source(conn, "无URL源")
    items = [_item("无URL一", url="", source_id=sid), _item("无URL二", url="", source_id=sid)]
    assert filtering.dedup_by_url(conn, items) == items


# ---------- 快照截断（spec 5.4） ----------

def test_truncate_snapshot_2000():
    text = "x" * 3000
    assert filtering.truncate_snapshot(text) == "x" * 2000


def test_truncate_snapshot_short_text_unchanged():
    assert filtering.truncate_snapshot("short") == "short"


def test_truncate_snapshot_custom_max_len():
    assert filtering.truncate_snapshot("abcde", max_len=3) == "abc"
