import json

import httpx

from idea_hub.collectors.base import BaseCollector, CollectorError, RawItem


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class ZhihuCollector(BaseCollector):
    type = "zhihu-hotlist"

    def fetch(self) -> list[RawItem]:
        try:
            config = json.loads(self.source_row.get("channel_config") or "{}")
            limit = config.get("limit", 50)
        except (TypeError, ValueError, json.JSONDecodeError):
            limit = 50

        try:
            response = httpx.get(
                "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50",
                headers={"User-Agent": USER_AGENT},
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            entries = payload["data"]
            if not isinstance(entries, list):
                raise TypeError("Zhihu data must be a list")

            items = []
            for entry in entries[:limit]:
                target = entry["target"]
                title = target["title"]
                url = target.get("url", "")
                if not isinstance(title, str) or not isinstance(url, str):
                    raise TypeError("Zhihu entry has invalid title or url")
                items.append(RawItem(
                    title=title,
                    url=url,
                    content_snapshot=f"热度:{entry.get('detail_text', '')}",
                    source_id=self.source_id,
                ))
            return items
        except Exception as exc:
            raise CollectorError(f"failed to parse Zhihu hotlist: {exc}") from exc
