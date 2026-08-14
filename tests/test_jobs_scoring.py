"""Task S3.2: collect job 集成评分测试。

Covers: collect 流程在 dedup 之后调用 score_items 并将
final_score/score_breakdown/verdict 写入 hot_items（admit/discard 两档都入库）；
无 key 降级全收（final_score=None 写 0）；有 key 路径走真实 scorer + mock LLM
（含 usage 计 token_used）；settings（score_dimensions / score_todo_threshold）
透传；通知文案区分"已评分（收录 X 丢弃 Y）"与"降级全收"。
"""
import json

import pytest

from idea_hub.collectors.base import RawItem
from idea_hub.services import jobs, scorer
from idea_hub.services.scorer import ScoredItem

DIMS = ["facts", "verification", "timeliness", "value"]


def _make_source(conn, url="https://example.com/feed"):
    cur = conn.execute(
        "INSERT INTO sources (type, name, url) VALUES (?, ?, ?)",
        ("hotlist", "测试来源", url),
    )
    conn.commit()
    return cur.lastrowid


def _fake_collect(raw_items):
    def fake(conn, source_ids=None):
        return {"items": raw_items, "errors": []}

    return fake


def _fake_llm_with_usage(rows_by_call, usage_by_call):
    """返回 (fake_post, calls)；按调用次数返回对应 rows/usage。"""
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        i = calls["n"]
        calls["n"] += 1
        rows = rows_by_call[i]
        usage = usage_by_call[i]

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "choices": [{"message": {
                        "content": json.dumps(rows, ensure_ascii=False)}}],
                    "usage": usage,
                }

        return FakeResp()

    return fake_post, calls


def _get_hot_items(conn):
    return conn.execute(
        "SELECT title, final_score, score_breakdown, verdict FROM hot_items"
    ).fetchall()


def test_collect_scores_items_when_key_present(conn, tmp_path, monkeypatch):
    """有 key：mock scorer 后 verdict 分流正确，admit/discard 两档都入库。"""
    source_id = _make_source(conn)
    job_id = jobs.create_job(conn, "collect", {})
    jobs.mark_running(job_id)

    raw = [
        RawItem(title="热点A", url="https://example.com/a",
                content_snapshot="内容A", source_id=source_id),
        RawItem(title="热点B", url="https://example.com/b",
                content_snapshot="内容B", source_id=source_id),
    ]
    monkeypatch.setattr("idea_hub.services.jobs.collect_all",
                        _fake_collect(raw))

    captured = {}

    def fake_score_items(items, api_key=None, dimensions=None, threshold=8,
                         token_usage=None):
        captured["api_key"] = api_key
        captured["dimensions"] = dimensions
        captured["threshold"] = threshold
        out = []
        for i, it in enumerate(items):
            if i % 2 == 0:
                out.append(ScoredItem(
                    title=it.title, url=it.url,
                    content_snapshot=it.content_snapshot, source_id=it.source_id,
                    final_score=9,
                    score_breakdown={"facts": 9, "verification": 9,
                                     "timeliness": 9, "value": 9},
                    verdict="admit"))
            else:
                out.append(ScoredItem(
                    title=it.title, url=it.url,
                    content_snapshot=it.content_snapshot, source_id=it.source_id,
                    final_score=3,
                    score_breakdown={"facts": 3, "verification": 3,
                                     "timeliness": 3, "value": 3},
                    verdict="discard"))
        return out

    monkeypatch.setattr("idea_hub.services.jobs.score_items", fake_score_items)

    jobs.run_collect_job(job_id, {}, str(tmp_path / "test.db"), api_key="sk-test")

    assert captured["api_key"] == "sk-test"
    assert captured["dimensions"] == DIMS
    assert captured["threshold"] == 8  # settings score_todo_threshold 默认

    rows = _get_hot_items(conn)
    by_title = {r["title"]: r for r in rows}
    assert set(by_title) == {"热点A", "热点B"}
    assert by_title["热点A"]["verdict"] == "admit"
    assert by_title["热点A"]["final_score"] == 9
    assert json.loads(by_title["热点A"]["score_breakdown"])["facts"] == 9
    assert by_title["热点B"]["verdict"] == "discard"  # discard 也入库
    assert by_title["热点B"]["final_score"] == 3

    job = conn.execute(
        "SELECT status, result_ref FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["status"] == "done"
    assert json.loads(job["result_ref"])["hotspot_count"] == 2

    notif = conn.execute(
        "SELECT body FROM notifications WHERE type = 'collect_done'"
    ).fetchone()
    assert notif is not None
    assert "收录 1 条 / 丢弃 1 条" in notif["body"]


def test_collect_falls_back_admit_without_key(conn, tmp_path, monkeypatch):
    """无 key：真实 scorer 降级全收，final_score=None 写 0、breakdown 写 '{}'。"""
    source_id = _make_source(conn)
    job_id = jobs.create_job(conn, "collect", {})
    jobs.mark_running(job_id)

    raw = [
        RawItem(title="热点A", url="https://example.com/a",
                content_snapshot="内容A", source_id=source_id),
    ]
    monkeypatch.setattr("idea_hub.services.jobs.collect_all", _fake_collect(raw))

    jobs.run_collect_job(job_id, {}, str(tmp_path / "test.db"))  # api_key=None

    rows = _get_hot_items(conn)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "admit"
    assert rows[0]["final_score"] == 0      # None -> 0 写入（NOT NULL 列）
    assert rows[0]["score_breakdown"] == "{}"

    job = conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["status"] == "done"

    notif = conn.execute(
        "SELECT body FROM notifications WHERE type = 'collect_done'"
    ).fetchone()
    assert notif is not None
    assert "降级全收" in notif["body"]


def test_collect_with_key_uses_mock_llm_and_counts_tokens(
    conn, tmp_path, monkeypatch
):
    """有 key + mock LLM：真实 score_items 打分、两档分流、usage 计入 token_used。"""
    s1 = _make_source(conn, url="https://one.example/feed")
    s2 = _make_source(conn, url="https://two.example/feed")
    job_id = jobs.create_job(conn, "collect", {})
    jobs.mark_running(job_id)

    raw1 = [
        RawItem(title="正常热点A", url="https://one.example/a",
                content_snapshot="内容A", source_id=s1),
    ]
    raw2 = [
        RawItem(title="正常热点B", url="https://two.example/b",
                content_snapshot="内容B", source_id=s2),
    ]

    def fake_collect(conn, source_ids=None):
        sid = source_ids[0]
        return {"items": raw1 if sid == s1 else raw2, "errors": []}

    monkeypatch.setattr("idea_hub.services.jobs.collect_all", fake_collect)

    rows_by_call = [
        [{"title": "正常热点A",
          "dimension_scores": {"facts": 9, "verification": 9,
                               "timeliness": 9, "value": 9},
          "final_score": 9}],
        [{"title": "正常热点B",
          "dimension_scores": {"facts": 5, "verification": 5,
                               "timeliness": 5, "value": 5},
          "final_score": 5}],
    ]
    usage_by_call = [
        {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        {"prompt_tokens": 200, "completion_tokens": 50, "total_tokens": 250},
    ]
    fake_post, calls = _fake_llm_with_usage(rows_by_call, usage_by_call)
    monkeypatch.setattr(scorer.httpx, "post", fake_post)

    jobs.run_collect_job(job_id, {}, str(tmp_path / "test.db"), api_key="sk-test")

    assert calls["n"] == 2  # 每来源一批次一次 LLM 调用
    by_title = {r["title"]: r for r in _get_hot_items(conn)}
    assert set(by_title) == {"正常热点A", "正常热点B"}
    assert by_title["正常热点A"]["verdict"] == "admit"     # 9 >= 8
    assert by_title["正常热点A"]["final_score"] == 9
    assert by_title["正常热点B"]["verdict"] == "discard"   # 5 < 8
    assert by_title["正常热点B"]["final_score"] == 5

    job = conn.execute(
        "SELECT token_used FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    assert job["token_used"] == 400  # 150 + 250


def test_collect_uses_settings_dimensions_and_threshold(
    conn, tmp_path, monkeypatch
):
    """settings 覆盖：score_dimensions / score_todo_threshold 透传给 score_items。"""
    from idea_hub.services import settings as settings_service

    settings_service.update(conn, "score_todo_threshold", 6)
    settings_service.update(conn, "score_dimensions", ["facts", "value"])
    source_id = _make_source(conn)
    job_id = jobs.create_job(conn, "collect", {})
    jobs.mark_running(job_id)

    raw = [
        RawItem(title="热点A", url="https://example.com/a",
                content_snapshot="内容A", source_id=source_id),
    ]
    monkeypatch.setattr("idea_hub.services.jobs.collect_all", _fake_collect(raw))

    captured = {}

    def fake_score_items(items, api_key=None, dimensions=None, threshold=8,
                         token_usage=None):
        captured["dimensions"] = dimensions
        captured["threshold"] = threshold
        return [
            ScoredItem(title=it.title, url=it.url,
                       content_snapshot=it.content_snapshot,
                       source_id=it.source_id, final_score=7,
                       score_breakdown={"facts": 7, "value": 7},
                       verdict="admit")
            for it in items
        ]

    monkeypatch.setattr("idea_hub.services.jobs.score_items", fake_score_items)

    jobs.run_collect_job(job_id, {}, str(tmp_path / "test.db"), api_key="sk-test")

    assert captured["dimensions"] == ["facts", "value"]
    assert captured["threshold"] == 6
    rows = _get_hot_items(conn)
    assert len(rows) == 1
    assert rows[0]["verdict"] == "admit"
