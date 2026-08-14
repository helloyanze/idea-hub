import httpx

from idea_hub.collectors.base import BaseCollector, CollectorError, RawItem

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class V2exCollector(BaseCollector):
    type = "v2ex"

    def fetch(self) -> list[RawItem]:
        limit = self.load_channel_config().get("limit", 50)
        try:
            response = httpx.get(
                "https://www.v2ex.com/api/topics/hot.json",
                headers=self.build_headers({"User-Agent": USER_AGENT}),
                timeout=10,
            )
            response.raise_for_status()
            topics = response.json()
            if not isinstance(topics, list):
                raise TypeError("V2EX payload must be a list")

            items = []
            for topic in topics[:limit]:
                title = topic["title"]
                url = topic["url"]
                if not isinstance(title, str) or not isinstance(url, str):
                    raise TypeError("V2EX topic has invalid title or url")
                items.append(RawItem(
                    title=title,
                    url=url,
                    content_snapshot=f"回复:{topic.get('replies', '')}",
                    source_id=self.source_id,
                ))
            return items
        except Exception as exc:
            raise CollectorError(f"failed to parse V2EX hotlist: {exc}") from exc
