"""Task S4.1: generate candidate selection (services/generate.py).

Covers: admit+高分入选、低于阈值排除、已关联 task 排除、过期排除（ttl 过期）、
ttl NULL 永不过期、hotspot_ids 显式指定（仍须满足 admit/未关联/未过期，仅从
指定集合筛选）、无评分（0/None）按 collected_at 排序、final_score 降序、
count 截断与 settings.generate_count 默认值。
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from idea_hub.services import generate
from idea_hub.services.settings import update

DEFAULT_COUNT = 10


def _ts(hours_ago=1):
    """UTC 时间戳字符串（与 sqlite datetime('now') 同格式），默认 1 小时前（未过期）。"""
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _add_source(conn, ttl_hours=24, name="测试来源"):
    cur = conn.execute(
        "INSERT INTO sources (type, name, url, ttl_hours) VALUES (?, ?, ?, ?)",
        ("hotlist", name, f"https://example.com/{name}", ttl_hours),
    )
    conn.commit()
    return cur.lastrowid


def _add_hotspot(
    conn,
    source_id,
    title="热点",
    url=None,
    final_score=9,
    verdict="admit",
    collected_at=None,
    score_breakdown=None,
):
    if collected_at is None:
        collected_at = _ts()
    cur = conn.execute(
        "INSERT INTO hot_items (source_id, title, url, content_snapshot, "
        "collected_at, final_score, score_breakdown, verdict) "
        "VALUES (?, ?, ?, '', ?, ?, ?, ?)",
        (
            source_id,
            title,
            url or f"https://example.com/{title}",
            collected_at,
            final_score,
            json.dumps(score_breakdown or {}),
            verdict,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _link_task(conn, hot_item_id):
    cur = conn.execute("INSERT INTO tasks (title) VALUES (?)", ("关联任务",))
    task_id = cur.lastrowid
    conn.execute(
        "INSERT INTO task_links (task_id, hot_item_id) VALUES (?, ?)",
        (task_id, hot_item_id),
    )
    conn.commit()


def _ids(items):
    return [item["hotspot_id"] for item in items]


def test_admit_high_score_selected_with_full_dict(conn):
    """admit + 高分入选，返回 dict 结构完整（审阅 #15 定稿字段）。"""
    source_id = _add_source(conn, ttl_hours=24)
    collected_at = _ts(hours_ago=2)
    hid = _add_hotspot(conn, source_id, title="高分热点", final_score=9,
                       score_breakdown={"facts": 9, "value": 9},
                       collected_at=collected_at)

    items = generate.get_candidates(conn)

    assert _ids(items) == [hid]
    item = items[0]
    assert item["hotspot_id"] == hid
    assert item["title"] == "高分热点"
    assert item["url"] == f"https://example.com/高分热点"
    assert item["source_id"] == source_id
    assert item["collected_at"] == collected_at
    assert item["ttl_hours"] == 24
    assert item["final_score"] == 9
    assert item["score_breakdown"] == {"facts": 9, "value": 9}


def test_below_threshold_excluded(conn):
    """admit 但 final_score 低于 score_todo_threshold（默认 8）排除。"""
    source_id = _add_source(conn)
    _add_hotspot(conn, source_id, title="低分", final_score=5)
    _add_hotspot(conn, source_id, title="边界分", final_score=7)

    assert generate.get_candidates(conn) == []


def test_discard_excluded(conn):
    """verdict=discard 排除，即使分数高。"""
    source_id = _add_source(conn)
    _add_hotspot(conn, source_id, title="丢弃", final_score=9, verdict="discard")

    assert generate.get_candidates(conn) == []


def test_linked_task_excluded(conn):
    """已关联 task（task_links 存在）排除。"""
    source_id = _add_source(conn)
    hid = _add_hotspot(conn, source_id, title="已关联", final_score=9)
    _link_task(conn, hid)

    assert generate.get_candidates(conn) == []


def test_expired_excluded_but_ttl_null_never_expires(conn):
    """过期（collected_at + ttl_hours <= now）排除；ttl NULL 永不过期。"""
    source_id_short = _add_source(conn, ttl_hours=24)
    source_id_null = _add_source(conn, ttl_hours=None)
    # 48 小时前收集，ttl=24 → 已过期
    _add_hotspot(conn, source_id_short, title="已过期",
                 collected_at=_ts(hours_ago=48), final_score=9)
    # 30 天前收集但 ttl NULL → 永不过期，仍入选
    hid = _add_hotspot(conn, source_id_null, title="无时效",
                       collected_at=_ts(hours_ago=24 * 30), final_score=9)

    items = generate.get_candidates(conn)

    assert _ids(items) == [hid]
    assert items[0]["ttl_hours"] is None


def test_hotspot_ids_restricts_set_but_still_filters(conn):
    """显式 hotspot_ids：仅从指定集合筛选，但仍须满足 admit/未关联/未过期。"""
    source_id = _add_source(conn)
    ok = _add_hotspot(conn, source_id, title="集合内合格", final_score=9)
    bad_discard = _add_hotspot(conn, source_id, title="集合内discard",
                               final_score=9, verdict="discard")
    linked = _add_hotspot(conn, source_id, title="集合内已关联", final_score=9)
    _link_task(conn, linked)
    outside = _add_hotspot(conn, source_id, title="集合外合格", final_score=9)

    items = generate.get_candidates(conn, hotspot_ids=[ok, bad_discard, linked])

    # 只返回集合内且满足条件的 ok；集合外的 outside 不因显式指定而入选
    assert _ids(items) == [ok]


def test_sorted_by_final_score_desc(conn):
    """按 final_score 降序。"""
    source_id = _add_source(conn)
    mid = _add_hotspot(conn, source_id, title="8分", final_score=8)
    high = _add_hotspot(conn, source_id, title="10分", final_score=10)
    low = _add_hotspot(conn, source_id, title="9分", final_score=9)

    assert _ids(generate.get_candidates(conn)) == [high, low, mid]


def test_unscored_sorted_by_collected_at_desc(conn):
    """无评分（final_score=0）热点按 collected_at 降序，且在评分热点之后。"""
    source_id = _add_source(conn)
    scored = _add_hotspot(conn, source_id, title="评分9", final_score=9,
                          collected_at=_ts(hours_ago=3))
    old_unscored = _add_hotspot(conn, source_id, title="无评分旧",
                                final_score=0, collected_at=_ts(hours_ago=6))
    new_unscored = _add_hotspot(conn, source_id, title="无评分新",
                                final_score=0, collected_at=_ts(hours_ago=1))

    items = generate.get_candidates(conn)

    # 评分热点在前（分数降序），无评分热点按 collected_at 降序
    assert _ids(items) == [scored, new_unscored, old_unscored]


def test_count_truncates(conn):
    """count 参数生效：只返回前 N 个。"""
    source_id = _add_source(conn)
    hids = [
        _add_hotspot(conn, source_id, title=f"热点{i}", final_score=10 - i)
        for i in range(5)
    ]

    items = generate.get_candidates(conn, count=2)

    assert _ids(items) == hids[:2]


def test_default_count_from_settings(conn):
    """count 未传时用 settings.generate_count（默认 10）。"""
    source_id = _add_source(conn)
    hids = [
        _add_hotspot(conn, source_id, title=f"热点{i}", final_score=20 - i)
        for i in range(12)
    ]

    items = generate.get_candidates(conn)

    assert len(items) == DEFAULT_COUNT
    assert _ids(items) == hids[:DEFAULT_COUNT]


def test_count_override_respects_settings_change(conn):
    """显式 count 优先于 settings.generate_count。"""
    update(conn, "generate_count", 3)
    source_id = _add_source(conn)
    hids = [
        _add_hotspot(conn, source_id, title=f"热点{i}", final_score=10 - i)
        for i in range(6)
    ]

    # 显式 count=2 优先
    assert _ids(generate.get_candidates(conn, count=2)) == hids[:2]
    # 未传 count 时用 settings 新值 3
    assert _ids(generate.get_candidates(conn)) == hids[:3]
