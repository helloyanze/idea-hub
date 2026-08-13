import json

import requests

from idea_hub.collectors.base import BaseCollector, RawItem


class HackerNewsCollector(BaseCollector):
    type = "hackernews"

    def fetch(self) -> list[RawItem]:
        try:
            config = json.loads(self.source_row.get("channel_config") or "{}")
            limit = config.get("limit", 30)
        except (TypeError, ValueError, json.JSONDecodeError):
            limit = 30

        resp = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=15
        )
        ids = resp.json()[:limit]
        items = []
        for hid in ids:
            try:
                item_resp = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{hid}.json", timeout=15
                )
                it = item_resp.json()
                if not it or it.get("type") != "story" or not it.get("title"):
                    continue
                hn_url = f"https://news.ycombinator.com/item?id={hid}"
                items.append(RawItem(
                    title=it["title"],
                    url=it.get("url") or hn_url,
                    content_snapshot=(
                        f"得分:{it.get('score', 0)} 评论:{it.get('descendants', 0)} HN:{hn_url}"
                    ),
                    source_id=self.source_id,
                ))
            except Exception:
                continue
        return items
