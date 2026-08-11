import json, pathlib, threading, subprocess, sys, time, http.server
from idea_hub import db, models

class Handler(http.server.BaseHTTPRequestHandler):
    payload = {"data": [{"title": "热点1", "url": "http://h1", "hot": 88}]}
    def do_GET(self):
        body = json.dumps(self.payload).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass

def test_full_pipeline(tmp_path):
    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    db_path = str(tmp_path / "e2e.db")
    base = tmp_path
    conn = db.connect(db_path); db.init_schema(conn)
    models.create_target(conn, name="自媒体", description="d", score_dimensions="{}")
    models.activate_target(conn, 1)
    models.create_source(conn, type="hotlist", name="测试榜", url=f"http://127.0.0.1:{port}/")
    conn.close()
    def cli(*args):
        return subprocess.run([sys.executable, "-m", "idea_hub.cli", "--db", db_path, "--base", str(base), *args],
                              capture_output=True, text=True, cwd=pathlib.Path(__file__).parent.parent)
    r = cli("collect"); assert r.returncode == 0, r.stderr
    assert "collected=1" in r.stdout
    cand = cli("candidates")
    assert "热点1" in cand.stdout
    draft = tmp_path / "d.md"; draft.write_text("# 构思\n全文", encoding="utf-8")
    r = cli("add-idea", "--hot-item-id", "1", "--title", "写热点1", "--summary", "摘要",
            "--score", "8", "--dims", '{"热度":8}', "--detail-path", str(draft))
    assert r.returncode == 0, r.stderr
    # add-idea 按阈值规则创建为 todo（Task 1）；next 只取 waiting（Task 5）。
    # 真实产品中 todo→waiting 由前端拖拽完成，此处用等价的最小操作入队：
    conn = db.connect(db_path)
    models.move_task(conn, 1, "waiting")
    conn.close()
    r = cli("next"); assert r.returncode == 0, r.stderr
    out = tmp_path / "o.md"; out.write_text("# 产出\n正文", encoding="utf-8")
    r = cli("complete", "--task-id", "1", "--summary", "完成", "--output-path", str(out))
    assert r.returncode == 0, r.stderr
    conn = db.connect(db_path)
    t = models.get_task(conn, 1)
    assert t["status"] == "done"
    assert pathlib.Path(base, "outputs/tasks/1/output.md").exists()
    conn.close(); srv.shutdown()
