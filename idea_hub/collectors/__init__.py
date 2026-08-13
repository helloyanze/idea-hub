from idea_hub.collectors.base import BaseCollector, RawItem
from idea_hub.collectors.github import GithubTrendingCollector
from idea_hub.collectors.hackernews import HackerNewsCollector
from idea_hub.collectors.hotlist import HotlistCollector
from idea_hub.collectors.rss import RssCollector
from idea_hub.collectors.v2ex import V2exCollector
from idea_hub.collectors.weibo import WeiboCollector
from idea_hub.collectors.zhihu import ZhihuCollector
from idea_hub import models


collector_registry: dict[str, type[BaseCollector]] = {
    HotlistCollector.type: HotlistCollector,
    RssCollector.type: RssCollector,
    GithubTrendingCollector.type: GithubTrendingCollector,
    HackerNewsCollector.type: HackerNewsCollector,
    ZhihuCollector.type: ZhihuCollector,
    WeiboCollector.type: WeiboCollector,
    V2exCollector.type: V2exCollector,
}


def collect_all(
    conn, source_ids: list[int] | None = None, limit_per_source: int = 50
) -> dict:
    """逐来源收集，返回收集结果及每个来源的错误。"""
    rows = models.list_sources(conn, enabled_only=source_ids is None)
    if source_ids is not None:
        source_id_set = set(source_ids)
        rows = [row for row in rows if row["id"] in source_id_set]

    items = []
    errors = []
    for row in rows:
        collector_class = collector_registry.get(row["type"])
        if collector_class is None:
            errors.append({
                "source_id": row["id"],
                "error": f"unknown source type: {row['type']}",
            })
            continue
        try:
            source_items = collector_class(row).fetch()[:limit_per_source]
            for item in source_items:
                item.source_id = row["id"]
            items.extend(source_items)
        except Exception as exc:
            errors.append({"source_id": row["id"], "error": str(exc)})
    return {"items": items, "errors": errors}
