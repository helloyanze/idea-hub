# tests/test_import_ideas_ext.py
"""import-ideas 扩展：content_type / expire_at 落库。"""
import json
import os
import subprocess
import sys
from pathlib import Path
from idea_hub import db, models

PROJECT_ROOT = Path(__file__).parent.parent

def _run(tmp_path: Path, file: Path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)  # cwd=tmp_path 时保证 idea_hub 可导入
    return subprocess.run([sys.executable, "-m", "idea_hub.cli", "--db",
                           str(tmp_path / "t.db"), "import-ideas", "--file", str(file)],
                          capture_output=True, text=True, cwd=str(tmp_path), env=env)

def test_import_content_type_and_expire(tmp_path):
    conn = db.connect(str(tmp_path / "t.db"))
    db.init_schema(conn)
    models.create_target(conn, name="自媒体内容", description="d", score_dimensions="{}")
    models.activate_target(conn, 1)
    conn.commit()
    data = [{"title": "短内容", "summary": "s", "score": 9, "dims": "{}",
             "detail": "构思", "content_type": "short",
             "expire_at": "2026-08-20T00:00:00"}]
    f = tmp_path / "ideas.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = _run(tmp_path, f)
    assert r.returncode == 0, r.stderr
    tasks = models.list_tasks(conn, status="todo")
    assert len(tasks) == 1
    assert tasks[0]["content_type"] == "short"
    assert tasks[0]["expire_at"] == "2026-08-20T00:00:00"
