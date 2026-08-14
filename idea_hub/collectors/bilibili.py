import requests

from idea_hub.collectors.base import BaseCollector, RawItem

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class BilibiliHotlistCollector(BaseCollector):
    """B站热门（公开接口，无需登录）。

    data.list[] 为视频条目（title/bvid），URL 由 bvid 拼接。
    """

    type = "bilibili-hotlist"
    default_url = "https://api.bilibili.com/x/web-interface/popular?ps=50"

    def fetch(self) -> list[RawItem]:
        config = self.load_channel_config()
        limit = config.get("limit", 50)
        if not isinstance(limit, int):
            limit = 50
        url = self.source_row.get("url") or self.default_url
        headers = self.build_headers({
            "User-Agent": USER_AGENT,
            "Referer": "https://www.bilibili.com/",
        })
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        entries = (resp.json().get("data") or {}).get("list") or []
        items = []
        for entry in entries[:limit]:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get("title", "")).strip()
            bvid = str(entry.get("bvid", "")).strip()
            if not title or not bvid:
                continue
            items.append(RawItem(
                title=title,
                url=f"https://www.bilibili.com/video/{bvid}",
                content_snapshot=str(entry.get("desc", "")).strip(),
                source_id=self.source_id,
            ))
        return items
