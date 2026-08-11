import sqlite3, pathlib, pytest
from idea_hub import db

@pytest.fixture()
def conn(tmp_path):
    c = db.connect(str(tmp_path / "test.db"))
    db.init_schema(c)
    yield c
    c.close()

@pytest.fixture()
def target_id(conn):
    conn.execute("INSERT INTO targets (name, description, score_dimensions, is_active) VALUES (?, ?, ?, 1)",
                 ("自媒体内容", "test", '{"热度":0.4,"相关性":0.3,"可执行性":0.3}'))
    conn.commit()
    return conn.execute("SELECT id FROM targets").fetchone()[0]
