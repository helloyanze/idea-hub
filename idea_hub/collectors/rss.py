import feedparser
import requests

from idea_hub.collectors.base import BaseCollector, RawItem


class RssCollector(BaseCollector):
    type = "rss"

    def fetch(self) -> list[RawItem]:
        resp = requests.get(self.source_row["url"], timeout=15)
        feed = feedparser.parse(resp.content)
        if feed.bozo:
            raise RuntimeError(f"feed parse failed: {feed.get('bozo_exception')}")
        items = []
        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            url = getattr(entry, "link", "").strip()
            if not title or not url:
                continue
            items.append(RawItem(
                title=title,
                url=url,
                content_snapshot=(getattr(entry, "summary", "") or "")[:500],
                source_id=self.source_id,
            ))
        return items
