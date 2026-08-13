# tests/test_tags_dedup.py
"""标签防重：同名标签返回已有 id，不重复创建。"""
from pathlib import Path

from idea_hub import db, models


def _conn(tmp_path: Path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    return conn


def test_create_tag_dedup(tmp_path):
    conn = _conn(tmp_path)
    t1 = models.create_tag(conn, name="防重测试标签", description="第一版")
    t2 = models.create_tag(conn, name="防重测试标签", description="第二版")
    assert t1 == t2  # 同名返回同一 id
    rows = models.list_tags(conn)
    assert len([t for t in rows if t["name"] == "防重测试标签"]) == 1
    # 描述不被覆盖（已存在直接返回）
    assert next(t for t in rows if t["name"] == "防重测试标签")["description"] == "第一版"


def test_create_tag_distinct_names(tmp_path):
    conn = _conn(tmp_path)
    t1 = models.create_tag(conn, name="防重测试A")
    t2 = models.create_tag(conn, name="防重测试B")
    assert t1 != t2
    names = {t["name"] for t in models.list_tags(conn)}
    assert "防重测试A" in names and "防重测试B" in names
