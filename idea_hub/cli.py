# idea_hub/cli.py
import argparse, json, pathlib, sys
from idea_hub import db, collectors, models

def _conn(args):
    c = db.connect(args.db)
    db.init_schema(c)
    return c

def cmd_collect(args):
    conn = _conn(args)
    res = collectors.collect_all(conn)
    print(f"collected={res['collected']}")
    for e in res["errors"]:
        print(f"ERROR: {e}", file=sys.stderr)

# ---- Task 4: idea generation + related-hotitem re-scoring primitives ----

def _link_exists(conn, task_id, hot_item_id):
    return conn.execute("SELECT 1 FROM task_links WHERE task_id=? AND hot_item_id=?",
                        (task_id, hot_item_id)).fetchone() is not None

def _write_draft(base, task_id, content):
    d = pathlib.Path(base) / "outputs" / "tasks" / str(task_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "idea.md"
    p.write_text(content, encoding="utf-8")
    return str(pathlib.Path("outputs") / "tasks" / str(task_id) / "idea.md").replace("\\", "/")

def cmd_candidates(args):
    conn = _conn(args)
    linked = {r["hot_item_id"] for r in conn.execute("SELECT hot_item_id FROM task_links").fetchall()}
    rows = conn.execute("SELECT id, title, url, content_snapshot FROM hot_items "
                        "WHERE date(collected_at)=date('now') ORDER BY id").fetchall()
    for r in rows:
        if r["id"] not in linked:
            print(json.dumps(dict(r), ensure_ascii=False))

def _todo_quota_ok(conn):
    """待办配额检查：todo 数量达到上限则返回 False（新 idea 应转留档）。"""
    limit = int(models.get_setting(conn, "todo_limit", "10"))
    count = conn.execute("SELECT COUNT(*) FROM tasks WHERE status='todo'").fetchone()[0]
    return count < limit

def cmd_import_ideas(args):
    """批量导入 idea（JSON 数组，每晚一次 LLM 调用生成）：
    每条含 hot_item_id/title/summary/score/dims/tags/detail；
    related_task_id 存在时走 relate 逻辑（更新已有任务+重评分+配额内自动移列）。"""
    conn = _conn(args)
    raw = pathlib.Path(args.file).read_text(encoding="utf-8")
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        import re as _re
        m = _re.search(r"```(?:json)?\s*(.*?)\s*```", raw, _re.S)
        if m:
            data = json.loads(m.group(1))
    if not isinstance(data, list):
        print("error: JSON 顶层必须是数组", file=sys.stderr)
        sys.exit(1)
    results = []
    for item in data:
        try:
            if item.get("related_task_id"):
                task = models.get_task(conn, item["related_task_id"])
                if task is None:
                    results.append({"hot_item_id": item.get("hot_item_id"), "error": "related task not found"})
                    continue
                addition = f"## 新增关联信息\n{item.get('addition', item.get('detail', ''))}"
                content = ""
                if task["idea_path"]:
                    p = pathlib.Path(task["idea_path"])
                    if p.exists():
                        content = p.read_text(encoding="utf-8")
                if item.get("hot_item_id") and not _link_exists(conn, task["id"], item["hot_item_id"]):
                    conn.execute("INSERT INTO task_links (task_id, hot_item_id) VALUES (?,?)",
                                 (task["id"], item["hot_item_id"]))
                models.update_task(conn, task["id"], feasibility_score=item["score"],
                                   score_breakdown=item["dims"],
                                   idea_path=_write_draft(args.base, task["id"], content + "\n\n" + addition))
                task2 = models.get_task(conn, task["id"])
                if task2["feasibility_score"] >= models.SCORE_THRESHOLD and task2["status"] == "archived":
                    if _todo_quota_ok(conn):
                        models.move_task(conn, task["id"], "todo")
                results.append({"task_id": task["id"],
                                "status": models.get_task(conn, task["id"])["status"], "relate": True})
            else:
                quota_full = not _todo_quota_ok(conn)  # 创建前检查
                tid = models.create_task(conn, title=item["title"], idea_summary=item["summary"],
                                         target_id=models.get_active_target(conn)["id"],
                                         hot_item_id=item.get("hot_item_id"),
                                         feasibility_score=item["score"],
                                         score_breakdown=item["dims"], idea_path="")
                if quota_full:
                    models.move_task(conn, tid, "archived")
                for tag_id in str(item.get("tags", "")).split(","):
                    tag_id = tag_id.strip()
                    if tag_id.isdigit():
                        models.add_task_tag(conn, tid, int(tag_id))
                models.update_task(conn, tid, idea_path=_write_draft(args.base, tid, item["detail"]))
                if item.get("hot_item_id"):
                    conn.execute("INSERT OR IGNORE INTO task_links (task_id, hot_item_id) VALUES (?,?)",
                                 (tid, item["hot_item_id"]))
                results.append({"task_id": tid,
                                "status": models.get_task(conn, tid)["status"], "relate": False})
            conn.commit()
        except Exception as exc:
            results.append({"error": str(exc)})
    print(json.dumps(results, ensure_ascii=False, indent=1))

def cmd_tags(args):
    """列出标签（供 AI 生成时选择）。"""
    conn = _conn(args)
    for t in models.list_tags(conn, active_only=getattr(args, "active", False)):
        print(json.dumps(dict(t), ensure_ascii=False))

def cmd_add_idea(args):
    conn = _conn(args)
    content = pathlib.Path(args.detail_path).read_text(encoding="utf-8")
    quota_full = not _todo_quota_ok(conn)  # 创建前检查（不含新任务）
    tid = models.create_task(conn, title=args.title, idea_summary=args.summary,
                             target_id=models.get_active_target(conn)["id"],
                             hot_item_id=args.hot_item_id, feasibility_score=args.score,
                             score_breakdown=args.dims, idea_path="")
    if quota_full:
        models.move_task(conn, tid, "archived")
    for tag_id in (args.tags or "").split(","):
        tag_id = tag_id.strip()
        if tag_id.isdigit():
            models.add_task_tag(conn, tid, int(tag_id))
    models.update_task(conn, tid, idea_path=_write_draft(args.base, tid, content))
    conn.execute("INSERT OR IGNORE INTO task_links (task_id, hot_item_id) VALUES (?,?)",
                 (tid, args.hot_item_id))
    conn.commit()
    print(tid)

def cmd_relate(args):
    conn = _conn(args)
    task = models.get_task(conn, args.task_id)
    if task is None:
        print(f"error: task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    # Read-only phase first: any failure here exits before a single write.
    addition = pathlib.Path(args.detail_path).read_text(encoding="utf-8")
    content = ""
    if task["idea_path"]:
        p = pathlib.Path(task["idea_path"])
        if p.exists():
            content = p.read_text(encoding="utf-8")
    # Write phase: task_links INSERT is no longer committed on its own;
    # everything lands in one commit at the end.
    if not _link_exists(conn, args.task_id, args.hot_item_id):
        conn.execute("INSERT INTO task_links (task_id, hot_item_id) VALUES (?,?)",
                     (args.task_id, args.hot_item_id))
    models.update_task(conn, args.task_id, feasibility_score=args.score,
                       score_breakdown=args.dims,
                       idea_path=_write_draft(args.base, args.task_id, content + "\n\n## 新增关联信息\n" + addition))
    task = models.get_task(conn, args.task_id)
    new_status = task["status"]
    if task["feasibility_score"] >= models.SCORE_THRESHOLD and task["status"] == "archived":
        if _todo_quota_ok(conn):
            models.move_task(conn, args.task_id, "todo"); new_status = "todo"
        else:
            new_status = "archived (todo quota full)"
    conn.commit()
    print(new_status)

# ---- Task 5: execution primitives (next / complete / fail) + execute_requests ----

def cmd_next(args):
    conn = _conn(args)
    if getattr(args, "task_id", None) is not None:
        # 定向领取：只领取指定 waiting 任务，校验失败时不领取任何任务
        task = models.get_task(conn, args.task_id)
        if task is None:
            print(f"error: task {args.task_id} not found", file=sys.stderr)
            sys.exit(1)
        if task["status"] != "waiting":
            print(f"error: task {args.task_id} status is {task['status']}, expected waiting",
                  file=sys.stderr)
            sys.exit(1)
        if not models.try_start_task(conn, args.task_id):
            print(f"error: task {args.task_id} could not be claimed", file=sys.stderr)
            sys.exit(1)
        task = models.get_task(conn, args.task_id)
        print(json.dumps(task, ensure_ascii=False))
        return
    row = conn.execute("SELECT id FROM tasks WHERE status='waiting' ORDER BY updated_at LIMIT 1").fetchone()
    if not row:
        print("queue empty"); sys.exit(1)
    if not models.try_start_task(conn, row["id"]):
        print("queue empty"); sys.exit(1)
    task = models.get_task(conn, row["id"])
    print(json.dumps(task, ensure_ascii=False))

def cmd_complete(args):
    conn = _conn(args)
    task = models.get_task(conn, args.task_id)
    if task is None:
        print(f"error: task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    content = pathlib.Path(args.output_path).read_text(encoding="utf-8")
    d = pathlib.Path(args.base) / "outputs" / "tasks" / str(args.task_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "output.md"
    p.write_text(content, encoding="utf-8")
    rel = str(pathlib.Path("outputs") / "tasks" / str(args.task_id) / "output.md").replace("\\", "/")
    models.update_task(conn, args.task_id, ai_summary=args.summary, output_path=rel)
    models.move_task(conn, args.task_id, "done")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (args.task_id,))
    conn.commit()
    print("done")

def cmd_fail(args):
    conn = _conn(args)
    task = models.get_task(conn, args.task_id)
    if task is None:
        print(f"error: task {args.task_id} not found", file=sys.stderr)
        sys.exit(1)
    models.update_task(conn, args.task_id, notes=f"{task['notes']}\n[失败] {args.reason}".strip())
    models.move_task(conn, args.task_id, "waiting")
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=? AND status='pending'",
                 (args.task_id,))
    conn.commit()
    print("waiting")

def cmd_pending_executions(args):
    conn = _conn(args)
    for r in conn.execute("SELECT task_id FROM execute_requests WHERE status='pending' ORDER BY id").fetchall():
        print(r["task_id"])

def cmd_resolve_execution(args):
    conn = _conn(args)
    conn.execute("UPDATE execute_requests SET status='done' WHERE task_id=?", (args.task_id,))
    conn.commit()

def _add_parser(sub, name, help_):
    return sub.add_parser(name, help=help_)

def main():
    p = argparse.ArgumentParser(prog="idea_hub")
    p.add_argument("--db", default="data/idea.db")
    p.add_argument("--base", default=str(pathlib.Path.cwd()))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("collect").set_defaults(func=cmd_collect)
    sub.add_parser("candidates").set_defaults(func=cmd_candidates)
    pa = sub.add_parser("add-idea")
    pa.add_argument("--hot-item-id", type=int, required=True)
    pa.add_argument("--title", required=True)
    pa.add_argument("--summary", required=True)
    pa.add_argument("--score", type=int, required=True)
    pa.add_argument("--dims", required=True)
    pa.add_argument("--detail-path", required=True)
    pa.add_argument("--tags", default="", help="标签 id 列表，逗号分隔（如 1,2,3）")
    pa.set_defaults(func=cmd_add_idea)
    pi = sub.add_parser("import-ideas")
    pi.add_argument("--file", required=True, help="idea JSON 文件路径（数组）")
    pi.set_defaults(func=cmd_import_ideas)
    pt = sub.add_parser("tags")
    pt.add_argument("--active", action="store_true", help="仅列出启用中的标签")
    pt.set_defaults(func=cmd_tags)
    pr = sub.add_parser("relate")
    pr.add_argument("--task-id", type=int, required=True)
    pr.add_argument("--hot-item-id", type=int, required=True)
    pr.add_argument("--score", type=int, required=True)
    pr.add_argument("--dims", required=True)
    pr.add_argument("--detail-path", required=True)
    pr.set_defaults(func=cmd_relate)
    pn = sub.add_parser("next")
    pn.add_argument("--task-id", type=int, default=None,
                    help="定向领取指定 waiting 任务（默认领取队首最早 waiting 任务）")
    pn.set_defaults(func=cmd_next)
    pc = sub.add_parser("complete")
    pc.add_argument("--task-id", type=int, required=True)
    pc.add_argument("--summary", required=True)
    pc.add_argument("--output-path", required=True)
    pc.set_defaults(func=cmd_complete)
    pf = sub.add_parser("fail")
    pf.add_argument("--task-id", type=int, required=True)
    pf.add_argument("--reason", required=True)
    pf.set_defaults(func=cmd_fail)
    sub.add_parser("pending-executions").set_defaults(func=cmd_pending_executions)
    prx = sub.add_parser("resolve-execution", add_help=False)
    prx.add_argument("--task-id", type=int, required=True)
    prx.set_defaults(func=cmd_resolve_execution)
    args = p.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
