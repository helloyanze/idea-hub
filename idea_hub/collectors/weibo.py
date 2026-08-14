from urllib.parse import quote

import httpx

from idea_hub.collectors.base import BaseCollector, CollectorError, RawItem

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class WeiboCollector(BaseCollector):
    type = "weibo-hotlist"

    def fetch(self) -> list[RawItem]:
        limit = self.load_channel_config().get("limit", 50)
        try:
            response = httpx.get(
                "https://weibo.com/ajax/side/hotSearch",
                headers=self.build_headers({"User-Agent": USER_AGENT}),
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
            entries = payload["data"]["realtime"]
            if not isinstance(entries, list):
                raise TypeError("Weibo realtime data must be a list")

            items = []
            for entry in entries[:limit]:
                word = entry["word"]
                if not isinstance(word, str):
                    raise TypeError("Weibo entry has invalid word")
                url = entry.get("word_scheme") or (
                    "https://s.weibo.com/weibo?q=" + quote(word)
                )
                if not isinstance(url, str):
                    raise TypeError("Weibo entry has invalid URL")
                items.append(RawItem(
                    title=word,
                    url=url,
                    content_snapshot=f"热度:{entry.get('num', '')}",
                    source_id=self.source_id,
                ))
            return items
        except Exception as exc:
            raise CollectorError(f"failed to parse Weibo hotlist: {exc}") from exc
