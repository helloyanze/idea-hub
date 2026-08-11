import pytest
from idea_hub import db, models

def test_create_and_get_task(conn, target_id):
    tid = models.create_task(conn, title="热点A文章", idea_summary="摘要",
                             target_id=target_id, feasibility_score=7,
                             score_breakdown='{"热度":8,"相关性":7,"可执行性":6}',
                             idea_path="outputs/tasks/1/idea.md")
    task = models.get_task(conn, tid)
    assert task["title"] == "热点A文章"
    assert task["status"] == "todo"  # score 7 >= 6
    assert task["feasibility_score"] == 7

def test_low_score_task_archived(conn, target_id):
    tid = models.create_task(conn, title="低分", idea_summary="s",
                             target_id=target_id, feasibility_score=4,
                             score_breakdown="{}", idea_path="x")
    assert models.get_task(conn, tid)["status"] == "archived"

def test_try_start_task_atomic(conn, target_id):
    tid = models.create_task(conn, title="t", idea_summary="s", target_id=target_id,
                             feasibility_score=7, score_breakdown="{}", idea_path="x")
    models.move_task(conn, tid, "waiting")
    assert models.try_start_task(conn, tid) is True
    assert models.get_task(conn, tid)["status"] == "in_progress"
    assert models.try_start_task(conn, tid) is False  # already in_progress

def test_stats_counts(conn, target_id):
    models.create_task(conn, title="a", idea_summary="s", target_id=target_id,
                       feasibility_score=7, score_breakdown="{}", idea_path="x")
    models.create_task(conn, title="b", idea_summary="s", target_id=target_id,
                       feasibility_score=4, score_breakdown="{}", idea_path="x")
    st = models.stats(conn)
    assert st["todo"] == 1 and st["archived"] == 1
