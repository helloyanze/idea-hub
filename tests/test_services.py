import pytest

from idea_hub import db, models, services


def _add_source(conn, name="Source"):
    return models.create_source(conn, type="hotlist", name=name, url="http://example.test")


def _add_task(conn, target_id, title, status="todo", score=8):
    task_id = models.create_task(
        conn,
        title=title,
        idea_summary=f"{title} summary",
        target_id=target_id,
        feasibility_score=score,
        score_breakdown="{}",
        idea_path="",
    )
    if status != models.get_task(conn, task_id)["status"]:
        models.move_task(conn, task_id, status)
    return task_id


def test_get_hotspot_summary_paginates_and_includes_source_and_link_count(conn, target_id):
    source_id = _add_source(conn, "Daily Source")
    conn.executemany(
        "INSERT INTO hot_items (source_id, title, url, collected_at) VALUES (?,?,?,?)",
        [
            (source_id, "Older", "http://older", "2026-08-11 01:00:00"),
            (source_id, "Newer", "http://newer", "2026-08-12 01:00:00"),
        ],
    )
    newer_id = conn.execute("SELECT id FROM hot_items WHERE title='Newer'").fetchone()["id"]
    task_id = _add_task(conn, target_id, "Linked task")
    conn.execute("INSERT INTO task_links (task_id, hot_item_id) VALUES (?,?)", (task_id, newer_id))
    conn.commit()

    result = services.get_hotspot_summary(conn, page=1, page_size=1)

    assert result["total"] == 2
    assert result["page"] == 1
    assert result["page_size"] == 1
    assert result["items"] == [
        {
            "id": newer_id,
            "source_id": source_id,
            "source_name": "Daily Source",
            "title": "Newer",
            "url": "http://newer",
            "content_snapshot": "",
            "collected_at": "2026-08-12 01:00:00",
            "final_score": 0.0,
            "review_status": "collected",
            "linked_task_count": 1,
        }
    ]
    assert services.get_hotspot_summary(conn, page=3, page_size=1)["items"] == []


def test_get_hotspot_summary_empty(conn):
    assert services.get_hotspot_summary(conn, page=1, page_size=20) == {
        "items": [], "total": 0, "page": 1, "page_size": 20
    }


def test_get_queue_summary_returns_all_statuses_for_empty_and_populated_queues(conn, target_id):
    assert services.get_queue_summary(conn) == {status: 0 for status in models.STATUSES}
    _add_task(conn, target_id, "Todo")
    _add_task(conn, target_id, "Waiting", status="waiting", score=7)

    summary = services.get_queue_summary(conn)

    assert summary == {"archived": 0, "todo": 1, "waiting": 1, "in_progress": 0, "done": 0}


def test_get_queue_items_validates_status_and_paginates_with_tags(conn, target_id):
    older_id = _add_task(conn, target_id, "Older")
    newer_id = _add_task(conn, target_id, "Newer")
    conn.execute("UPDATE tasks SET updated_at='2026-08-11 01:00:00' WHERE id=?", (older_id,))
    conn.execute("UPDATE tasks SET updated_at='2026-08-12 01:00:00' WHERE id=?", (newer_id,))
    tag_id = models.create_tag(conn, name="service-test")
    models.add_task_tag(conn, newer_id, tag_id)

    result = services.get_queue_items(conn, status="todo", page=1, page_size=1)

    assert result["total"] == 2
    assert result["page"] == 1
    assert result["page_size"] == 1
    assert result["items"][0]["id"] == newer_id
    assert result["items"][0]["tags"] == [{"id": tag_id, "name": "service-test"}]
    assert services.get_queue_items(conn, status="done", page=1, page_size=10)["items"] == []
    with pytest.raises(ValueError, match="bad status bogus"):
        services.get_queue_items(conn, status="bogus", page=1, page_size=10)


def test_collect_ideas_initializes_db_and_reuses_collect_all(tmp_path, monkeypatch):
    db_path = str(tmp_path / "collect.db")
    expected = {"collected": 2, "discarded": 1, "review": 0, "errors": []}
    seen = {}

    def fake_collect_all(conn):
        seen["schema_ready"] = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='hot_items'"
        ).fetchone()[0] == 1
        return expected

    monkeypatch.setattr("idea_hub.collectors.collect_all", fake_collect_all)

    assert services.collect_ideas(db_path, tmp_path) == expected
    assert seen == {"schema_ready": True}


def test_generate_ideas_reports_needs_agent_without_claiming_generation(tmp_path):
    db_path = str(tmp_path / "generate.db")
    conn = db.connect(db_path)
    db.init_schema(conn)
    source_id = _add_source(conn)
    conn.execute(
        "INSERT INTO hot_items (source_id, title, url) VALUES (?,?,?)",
        (source_id, "Candidate", "http://candidate"),
    )
    conn.commit()
    conn.close()

    result = services.generate_ideas(db_path, tmp_path)

    assert result == {
        "status": "needs-agent",
        "generated": 0,
        "candidate_count": 1,
        "message": "Idea generation requires the Hermes agent workflow.",
        "next_step": "Run candidates, generate ideas with Hermes, then import-ideas.",
    }
