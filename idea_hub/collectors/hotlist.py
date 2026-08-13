import requests

from idea_hub.collectors.base import BaseCollector, RawItem


def _dig(obj, path):
    parts = path.split(".")
    has_wildcard = any(part.endswith("[]") for part in parts)

    def resolve(value, remaining):
        if not remaining:
            return value, False

        part = remaining[0]
        rest = remaining[1:]

        if part.endswith("]") and "[" in part:
            key, bracket = part[:-1].rsplit("[", 1)
            if key:
                if not isinstance(value, dict):
                    return None, False
                value = value.get(key)

            if bracket == "":
                if not isinstance(value, list):
                    return [], True
                results = []
                for item in value:
                    result, expanded = resolve(item, rest)
                    if result is None:
                        continue
                    if expanded:
                        results.extend(result)
                    else:
                        results.append(result)
                return results, True

            if bracket.isdigit():
                if not isinstance(value, list):
                    return None, False
                index = int(bracket)
                if index >= len(value):
                    return None, False
                return resolve(value[index], rest)

        if isinstance(value, dict):
            return resolve(value.get(part), rest)
        if isinstance(value, list) and part.isdigit():
            index = int(part)
            if index >= len(value):
                return None, False
            return resolve(value[index], rest)
        return None, False

    result, _ = resolve(obj, parts)
    if result is None and has_wildcard:
        return []
    return result


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
