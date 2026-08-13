"""Task S2.8: notify 服务（提前，供 S2.5/S3.2/S4.3/S5.5/S6.x 使用）。

services/notify.emit —— 薄插入封装 + type/level 枚举校验（spec 5.4）：
- type: collect_done / generate_done / execute_done / job_failed /
  task_expired / budget_exceeded / discard_cleaned
- level: info / warn / error
"""
import pytest

from idea_hub import db
from idea_hub.services import notify


@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_schema(c)
    yield c
    c.close()


def _row(conn, nid):
    return conn.execute(
        "SELECT * FROM notifications WHERE id = ?", (nid,)
    ).fetchone()


# ---------- emit 基本写入 ----------

def test_emit_writes_all_fields(conn):
    nid = notify.emit(
        conn, "collect_done", "采集完成", "采集到 12 条热点", "info",
        entity_type="task", entity_id=7,
    )
    row = _row(conn, nid)
    assert row["type"] == "collect_done"
    assert row["title"] == "采集完成"
    assert row["body"] == "采集到 12 条热点"
    assert row["level"] == "info"
    assert row["entity_type"] == "task"
    assert row["entity_id"] == 7
    assert row["is_read"] == 0
    assert row["created_at"]


def test_emit_returns_incrementing_id(conn):
    nid1 = notify.emit(conn, "job_failed", "任务失败", "", "error")
    nid2 = notify.emit(conn, "job_failed", "任务失败", "", "error")
    assert isinstance(nid1, int) and nid1 > 0
    assert nid2 == nid1 + 1


# ---------- entity 关联可空 ----------

def test_emit_entity_optional(conn):
    nid = notify.emit(conn, "budget_exceeded", "预算超限", "今日预算已用尽", "warn")
    row = _row(conn, nid)
    assert row["entity_type"] is None
    assert row["entity_id"] is None


def test_emit_commits_to_db(conn, tmp_path):
    """emit 必须 commit：新连接可见（S6.x 任务服务用独立连接读通知）。"""
    nid = notify.emit(conn, "collect_done", "标题", "", "info")
    c2 = db.connect(str(tmp_path / "test.db"))
    try:
        row = c2.execute(
            "SELECT * FROM notifications WHERE id = ?", (nid,)
        ).fetchone()
        assert row is not None
    finally:
        c2.close()


# ---------- type/level 枚举校验 ----------

def test_emit_validates_type_enum(conn):
    with pytest.raises(ValueError):
        notify.emit(conn, "not_a_real_type", "标题", "", "info")


def test_emit_validates_level_enum(conn):
    with pytest.raises(ValueError):
        notify.emit(conn, "collect_done", "标题", "", "fatal")
