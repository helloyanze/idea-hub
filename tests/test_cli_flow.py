import json, pathlib, subprocess, sys
from idea_hub import db, models

def _run_cli(args, tmp_path):
    return subprocess.run([sys.executable, "-m", "idea_hub.cli", "--db", str(tmp_path / "t.db"), *args],
                          capture_output=True, text=True, cwd=pathlib.Path(__file__).parent.parent)

def _seed(conn):
    models.create_target(conn, name="自媒体内容", description="d", score_dimensions="{}")
    models.activate_target(conn, 1)
    models.create_source(conn, type="hotlist", name="榜", url="http://x")
    conn.execute("INSERT INTO hot_items (source_id, title, url, content_snapshot) VALUES (1, '热点X', 'http://x', 'snap')")
    conn.commit()

def test_add_idea_threshold_and_draft(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn); conn.close()
    draft = tmp_path / "draft.md"; draft.write_text("# 构思全文\n内容", encoding="utf-8")
    r = _run_cli(["add-idea", "--hot-item-id", "1", "--title", "写一篇X文章",
                  "--summary", "摘要", "--score", "7", "--dims", '{"热度":8}',
                  "--detail-path", str(draft)], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    task = models.get_task(conn, 1)
    assert task["status"] == "todo"
    assert pathlib.Path("outputs/tasks/1/idea.md").exists()
    conn.close()

def test_relate_rescores_archived_to_todo(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="旧想法", idea_summary="s", target_id=1,
                       hot_item_id=1, feasibility_score=5, score_breakdown="{}",
                       idea_path="outputs/tasks/1/idea.md")  # archived
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点Y', 'http://y')")
    conn.commit(); conn.close()
    draft = tmp_path / "draft2.md"; draft.write_text("补充信息", encoding="utf-8")
    r = _run_cli(["relate", "--task-id", "1", "--hot-item-id", "2",
                  "--score", "7", "--dims", '{"热度":9}', "--detail-path", str(draft)], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    task = models.get_task(conn, 1)
    assert task["status"] == "todo"
    assert task["feasibility_score"] == 7
    links = conn.execute("SELECT hot_item_id FROM task_links WHERE task_id=1").fetchall()
    assert {l["hot_item_id"] for l in links} == {1, 2}
    conn.close()

def test_relate_missing_task_fails_without_side_effects(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点Y', 'http://y')")
    conn.commit(); conn.close()
    draft = tmp_path / "draft.md"; draft.write_text("补充", encoding="utf-8")
    r = _run_cli(["relate", "--task-id", "999", "--hot-item-id", "2",
                  "--score", "7", "--dims", "{}", "--detail-path", str(draft)], tmp_path)
    assert r.returncode == 1, r.stderr
    assert "999" in r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    assert conn.execute("SELECT COUNT(*) FROM task_links").fetchone()[0] == 0
    conn.close()

def test_relate_missing_detail_file_leaves_no_orphan_link(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="旧想法", idea_summary="s", target_id=1,
                       hot_item_id=1, feasibility_score=5, score_breakdown="{}",
                       idea_path="outputs/tasks/1/idea.md")  # archived
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点Y', 'http://y')")
    conn.commit(); conn.close()
    r = _run_cli(["relate", "--task-id", "1", "--hot-item-id", "2",
                  "--score", "7", "--dims", '{"热度":9}',
                  "--detail-path", str(tmp_path / "missing.md")], tmp_path)
    assert r.returncode != 0  # FileNotFoundError propagates, no writes happened
    conn = db.connect(str(tmp_path / "t.db"))
    links = conn.execute("SELECT hot_item_id FROM task_links WHERE task_id=1").fetchall()
    assert {l["hot_item_id"] for l in links} == {1}  # no orphan link to hot item 2
    task = models.get_task(conn, 1)
    assert task["feasibility_score"] == 5  # score untouched
    assert task["status"] == "archived"  # status untouched
    conn.close()

def test_next_complete_fail_cycle(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="任务", idea_summary="s", target_id=1, hot_item_id=1,
                       feasibility_score=7, score_breakdown="{}", idea_path="")
    models.move_task(conn, 1, "waiting")
    conn.close()
    r = _run_cli(["next"], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    assert models.get_task(conn, 1)["status"] == "in_progress"
    conn.close()
    out = tmp_path / "out.md"; out.write_text("# 产出\n正文", encoding="utf-8")
    r = _run_cli(["complete", "--task-id", "1", "--summary", "完成摘要",
                  "--output-path", str(out)], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    t = models.get_task(conn, 1)
    assert t["status"] == "done" and t["ai_summary"] == "完成摘要"
    assert pathlib.Path("outputs/tasks/1/output.md").exists()
    conn.close()

def test_fail_returns_to_waiting(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="任务", idea_summary="s", target_id=1, hot_item_id=1,
                       feasibility_score=7, score_breakdown="{}", idea_path="")
    models.move_task(conn, 1, "waiting"); conn.close()
    _run_cli(["next"], tmp_path)
    r = _run_cli(["fail", "--task-id", "1", "--reason", "超时"], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    t = models.get_task(conn, 1)
    assert t["status"] == "waiting" and "超时" in t["notes"]
    conn.close()

def test_complete_missing_task_fails_cleanly(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn); conn.close()
    out = tmp_path / "out.md"; out.write_text("# 产出\n正文", encoding="utf-8")
    r = _run_cli(["complete", "--task-id", "999", "--summary", "完成摘要",
                  "--output-path", str(out)], tmp_path)
    assert r.returncode != 0
    assert "999" in r.stderr
    # no orphan output file / directory for the missing task
    assert not pathlib.Path("outputs/tasks/999/output.md").exists()
    assert not pathlib.Path("outputs/tasks/999").exists()

def test_fail_missing_task_fails_cleanly(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn); conn.close()
    r = _run_cli(["fail", "--task-id", "999", "--reason", "超时"], tmp_path)
    assert r.returncode != 0
    assert "999" in r.stderr

# ---- B1: next --task-id 定向领取（execute cron 按 pending id 领取，不得误领队首） ----

def test_next_task_id_claims_specified_not_head(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="早期任务", idea_summary="s", target_id=1, hot_item_id=1,
                       feasibility_score=7, score_breakdown="{}", idea_path="")
    models.create_task(conn, title="目标任务", idea_summary="s", target_id=1, hot_item_id=1,
                       feasibility_score=7, score_breakdown="{}", idea_path="")
    models.move_task(conn, 1, "waiting")  # 更早 waiting，队首
    models.move_task(conn, 2, "waiting")  # 指定领取的目标
    conn.close()
    r = _run_cli(["next", "--task-id", "2"], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    assert models.get_task(conn, 2)["status"] == "in_progress"  # 指定任务被领取
    assert models.get_task(conn, 1)["status"] == "waiting"      # 队首未被误领
    conn.close()

def test_next_task_id_rejects_non_waiting(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.create_task(conn, title="待办任务", idea_summary="s", target_id=1, hot_item_id=1,
                       feasibility_score=7, score_breakdown="{}", idea_path="")  # status=todo
    models.create_task(conn, title="等待任务", idea_summary="s", target_id=1, hot_item_id=1,
                       feasibility_score=7, score_breakdown="{}", idea_path="")
    models.move_task(conn, 2, "waiting")
    conn.close()
    r = _run_cli(["next", "--task-id", "1"], tmp_path)
    assert r.returncode == 1
    assert "1" in r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    assert models.get_task(conn, 1)["status"] == "todo"     # 状态不变
    assert models.get_task(conn, 2)["status"] == "waiting"  # 未领取任何任务
    conn.close()

def test_next_task_id_missing_task(tmp_path):
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn); conn.close()
    r = _run_cli(["next", "--task-id", "999"], tmp_path)
    assert r.returncode == 1
    assert "999" in r.stderr

def test_add_idea_todo_quota(tmp_path):
    """待办上限：todo 满时新 idea 自动转留档。"""
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    models.set_setting(conn, "todo_limit", "3")
    for i in range(3):
        models.create_task(conn, title=f"任务{i}", idea_summary="s", target_id=1,
                           feasibility_score=7, score_breakdown="{}", idea_path="")
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点Q', 'http://q')")
    conn.commit(); conn.close()
    draft = tmp_path / "d3.md"; draft.write_text("构思", encoding="utf-8")
    r = _run_cli(["add-idea", "--hot-item-id", "2", "--title", "超额任务",
                  "--summary", "s", "--score", "7", "--dims", "{}",
                  "--detail-path", str(draft)], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    # 第 4 个任务因配额进留档
    row = conn.execute("SELECT status FROM tasks WHERE title='超额任务'").fetchone()
    assert row["status"] == "archived"
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE status='todo'").fetchone()[0] == 3
    conn.close()

def test_add_idea_with_tags(tmp_path):
    """add-idea --tags 写入任务标签。"""
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    tag_id = models.create_tag(conn, name="agent")
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点T', 'http://t')")
    conn.commit(); conn.close()
    draft = tmp_path / "d4.md"; draft.write_text("构思", encoding="utf-8")
    r = _run_cli(["add-idea", "--hot-item-id", "2", "--title", "带标签任务",
                  "--summary", "s", "--score", "7", "--dims", "{}",
                  "--detail-path", str(draft), "--tags", str(tag_id)], tmp_path)
    assert r.returncode == 0, r.stderr
    conn = db.connect(str(tmp_path / "t.db"))
    tags = conn.execute("SELECT t.name FROM task_tags tt JOIN tags t ON t.id=tt.tag_id").fetchall()
    assert tags[0]["name"] == "agent"
    conn.close()

def test_import_ideas_batch(tmp_path):
    """批量导入：新 idea + 关联更新 + 配额 + 标签。"""
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    tag1 = models.create_tag(conn, name="ai")
    models.set_setting(conn, "todo_limit", "2")
    t1 = models.create_task(conn, title="已有任务", idea_summary="s", target_id=1,
                            feasibility_score=5, score_breakdown="{}", idea_path="")
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点A', 'http://a')")
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点B', 'http://b')")
    conn.commit(); conn.close()
    ideas = [
        {"hot_item_id": 2, "title": "新idea", "summary": "新", "score": 7,
         "dims": "{}", "tags": str(tag1),
         "detail": "构思全文内容\n第二行"},
        {"hot_item_id": 3, "related_task_id": t1, "score": 7, "dims": "{}",
         "detail": "新角度"},
    ]
    f = tmp_path / "ideas.json"; f.write_text(json.dumps(ideas, ensure_ascii=False), encoding="utf-8")
    r = _run_cli(["import-ideas", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert len(out) == 2
    new_task = out[0]
    assert new_task["relate"] is False and new_task["status"] == "todo"
    # 关联更新：已有任务分数 5→7 且 archived→todo（配额 2 允许）
    relate_task = out[1]
    assert relate_task["relate"] is True and relate_task["status"] == "todo"
    conn = db.connect(str(tmp_path / "t.db"))
    t2 = models.get_task(conn, t1)
    assert t2["feasibility_score"] == 7 and t2["status"] == "todo"
    tags = models.list_task_tags(conn, new_task["task_id"])
    assert tags[0]["name"] == "ai"
    assert models.get_task(conn, new_task["task_id"])["idea_path"] != ""
    conn.close()

def test_import_ideas_fenced_json(tmp_path):
    """容错：文件内容是 markdown 代码块包裹的 JSON 也能解析。"""
    conn = db.connect(str(tmp_path / "t.db")); db.init_schema(conn); _seed(conn)
    conn.execute("INSERT INTO hot_items (source_id, title, url) VALUES (1, '热点C', 'http://c')")
    conn.commit(); conn.close()
    f = tmp_path / "ideas.md"
    f.write_text('```json\n[{"hot_item_id": 2, "title": "块内idea", "summary": "s", '
                 '"score": 6, "dims": "{}", "tags": "", "detail": "内容"}]\n```\n', encoding="utf-8")
    r = _run_cli(["import-ideas", "--file", str(f)], tmp_path)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out[0]["status"] == "todo"
