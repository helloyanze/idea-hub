import requests

from idea_hub.collectors.base import BaseCollector, RawItem
from idea_hub.collectors.hotlist import _dig

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class BaiduHotlistCollector(BaseCollector):
    """百度热榜（公开接口，无需登录）。

    data.cards[].content[].content 为榜单条目（word/url）。
    """

    type = "baidu-hotlist"
    default_url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
    default_items_path = "data.cards[].content[].content"
    default_title_field = "word"

    def fetch(self) -> list[RawItem]:
        url = self.source_row.get("url") or self.default_url
        items_path = self.source_row.get("items_path") or self.default_items_path
        title_field = self.source_row.get("title_field") or self.default_title_field
        headers = self.build_headers({"User-Agent": USER_AGENT})
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        entries = _dig(resp.json(), items_path) or []
        items = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = str(entry.get(title_field, "")).strip()
            url_ = str(entry.get("url", "")).strip()
            if not title or not url_:
                continue
            items.append(RawItem(
                title=title,
                url=url_,
                content_snapshot="",
                source_id=self.source_id,
            ))
        return items
