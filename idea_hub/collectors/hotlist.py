import requests

from idea_hub.collectors.base import BaseCollector, RawItem


def _dig(obj, path):
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit():
            obj = obj[int(part)]
        else:
            return None
    return obj


class HotlistCollector(BaseCollector):
    type = "hotlist"

    def fetch(self) -> list[RawItem]:
        url = self.source_row["url"]
        items_path = self.source_row.get("items_path") or "data"
        title_field = self.source_row.get("title_field") or "title"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = _dig(resp.json(), items_path) or []
        items = []
        for it in data:
            if not isinstance(it, dict):
                continue
            title = str(it.get(title_field, "")).strip()
            url_ = str(it.get("url", "")).strip()
            if not title or not url_:
                continue
            parts = []
            if it.get("hot") is not None:
                parts.append(f"热度:{it['hot']}")
            if it.get("rank") is not None:
                parts.append(f"排名:{it['rank']}")
            if it.get("desc"):
                parts.append(str(it["desc"]))
            items.append(RawItem(
                title=title,
                url=url_,
                content_snapshot=" ".join(parts),
                source_id=self.source_id,
            ))
        return items
