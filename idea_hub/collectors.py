# idea_hub/collectors.py
import json
import requests
import feedparser
from idea_hub import models

def _dig(obj, path):
    for part in path.split("."):
        if isinstance(obj, dict): obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit(): obj = obj[int(part)]
        else: return None
    return obj

def fetch_hotlist(url, items_path="data", session=None):
    s = session or requests
    resp = s.get(url, timeout=15)
    resp.raise_for_status()
    data = _dig(resp.json(), items_path) or []
    out = []
    for it in data:
        item = {"title": str(it.get("title", "")).strip(),
                "url": str(it.get("url", "")).strip()}
        parts = []
        if it.get("hot") is not None: parts.append(f"热度:{it['hot']}")
        if it.get("rank") is not None: parts.append(f"排名:{it['rank']}")
        if it.get("desc"): parts.append(str(it["desc"]))
        item["content_snapshot"] = " ".join(parts)
        if item["title"] and item["url"]:
            out.append(item)
    return out

def fetch_rss(url):
    # requests.get(timeout=15)：底层 urllib 无超时，死 feed 会无限挂起
    resp = requests.get(url, timeout=15)
    feed = feedparser.parse(resp.content)
    if feed.bozo:
        raise RuntimeError(f"feed parse failed: {feed.get('bozo_exception')}")
    out = []
    for e in feed.entries:
        out.append({"title": getattr(e, "title", "").strip(),
                    "url": getattr(e, "link", "").strip(),
                    "content_snapshot": (getattr(e, "summary", "") or "")[:500]})
    return [i for i in out if i["title"] and i["url"]]

def _upsert_hot_item(conn, source_id, item):
    cur = conn.execute("INSERT OR IGNORE INTO hot_items (source_id, title, url, content_snapshot) "
                       "VALUES (?,?,?,?)", (source_id, item["title"], item["url"], item["content_snapshot"]))
    return cur.rowcount

def collect_all(conn):
    collected, errors = 0, []
    for src in models.list_sources(conn, enabled_only=True):
        try:
            items = fetch_rss(src["url"]) if src["type"] == "rss" else fetch_hotlist(src["url"])
            for it in items:
                collected += _upsert_hot_item(conn, src["id"], it)
            conn.commit()
        except Exception as exc:
            errors.append(f"{src['name']}: {exc}")
    return {"collected": collected, "errors": errors}
