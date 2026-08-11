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

def fetch_hotlist(url, items_path="data", title_field="title", session=None):
    s = session or requests
    resp = s.get(url, timeout=15)
    resp.raise_for_status()
    data = _dig(resp.json(), items_path) or []
    out = []
    for it in data:
        if not isinstance(it, dict):
            continue
        item = {"title": str(it.get(title_field, "")).strip(),
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

def _matches_keywords(item, keywords):
    """关键词白名单过滤：title 或快照含任一关键词（不区分大小写）则保留。"""
    if not keywords:
        return True
    text = f"{item['title']} {item['content_snapshot']}".lower()
    return any(k.strip().lower() in text for k in keywords.split(",") if k.strip())

def fetch_github_trending(url="https://github.com/trending?since=daily", session=None):
    """GitHub Trending 解析（参照 ai-opportunity-spider 思路）。"""
    from bs4 import BeautifulSoup
    s = session or requests
    resp = s.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for article in soup.select("article.Box-row"):
        h2 = article.select_one("h2 a")
        if not h2:
            continue
        repo = h2.get_text(" ", strip=True).replace(" ", "")
        desc = article.select_one("p")
        desc_text = desc.get_text(strip=True) if desc else ""
        stars = article.select_one("span.d-inline-block.float-sm-right")
        stars_text = stars.get_text(strip=True) if stars else ""
        out.append({
            "title": repo,
            "url": f"https://github.com{article.select_one('h2 a')['href']}",
            "content_snapshot": f"{stars_text} {desc_text}".strip(),
        })
    return out

def fetch_hackernews(limit=30, session=None):
    """HackerNews 热帖（Firebase 官方 API，无鉴权）。"""
    import json as _json
    s = session or requests
    resp = s.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=15)
    resp.raise_for_status()
    ids = _json.loads(resp.text)[:limit]
    out = []
    for hid in ids:
        try:
            r = s.get(f"https://hacker-news.firebaseio.com/v0/item/{hid}.json", timeout=15)
            it = _json.loads(r.text)
            if not it or it.get("type") != "story" or not it.get("title"):
                continue
            out.append({
                "title": it["title"],
                "url": it.get("url") or f"https://news.ycombinator.com/item?id={hid}",
                "content_snapshot": (f"得分:{it.get('score', 0)} 评论:{it.get('descendants', 0)} "
                                     f"HN:https://news.ycombinator.com/item?id={hid}"),
            })
        except Exception:
            continue
    return out

def _upsert_hot_item(conn, source_id, item, score=None):
    score = score or {}
    cur = conn.execute(
        "INSERT OR IGNORE INTO hot_items (source_id, title, url, content_snapshot, "
        "source_score, fact_score, verify_score, time_score, final_score, review_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (source_id, item["title"], item["url"], item["content_snapshot"],
         score.get("source_score", 0), score.get("fact_score", 0),
         score.get("verify_score", 60), score.get("time_score", 100),
         score.get("final_score", 0), score.get("review_status", "collected")))
    return cur.rowcount

def collect_all(conn, use_scoring=True):
    """收集全部分发：按来源类型抓取 → 关键词过滤 → 评分分流（收录/复核/丢弃）入库。

    use_scoring=False 时跳过 LLM 评分（测试与降级路径）。
    """
    from . import scorer as _scorer
    collected, discarded, review, errors = 0, 0, 0, []
    pending = []  # (src, item)
    for src in models.list_sources(conn, enabled_only=True):
        try:
            if src["type"] == "rss":
                items = fetch_rss(src["url"])
            elif src["type"] == "github-trending":
                items = fetch_github_trending(src["url"] or "https://github.com/trending?since=daily")
            elif src["type"] == "hackernews":
                items = fetch_hackernews()
            else:
                items = fetch_hotlist(
                    src["url"], items_path=src.get("items_path", "data"),
                    title_field=src.get("title_field", "title"))
            items = [it for it in items if _matches_keywords(it, src.get("keywords", ""))]
            pending.extend((src, it) for it in items)
        except Exception as exc:
            errors.append(f"{src['name']}: {exc}")
    if use_scoring and pending:
        try:
            scores = _scorer.score_batch([
                {"id": i, "title": it["title"], "url": it["url"],
                 "content_snapshot": it["content_snapshot"], "collected_at": None}
                for i, (_, it) in enumerate(pending)])
            for i, (src, it) in enumerate(pending):
                sc = scores.get(i, {})
                if sc.get("review_status") == "discarded":
                    discarded += 1
                    continue
                if sc.get("review_status") == "review":
                    review += 1
                collected += _upsert_hot_item(conn, src["id"], it, sc)
            conn.commit()
        except Exception as exc:
            errors.append(f"scoring: {exc}")
            # LLM 评分失败降级：全部按无评分入库，不阻塞收集
            for src, it in pending:
                collected += _upsert_hot_item(conn, src["id"], it)
            conn.commit()
    else:
        for src, it in pending:
            collected += _upsert_hot_item(conn, src["id"], it)
        conn.commit()
    return {"collected": collected, "discarded": discarded, "review": review, "errors": errors}
