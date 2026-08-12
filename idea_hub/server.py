import base64, hmac, json, os, pathlib
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from idea_hub import db, models, services

# ---- Basic Auth（公网部署启用：设置 IDEAHUB_AUTH_USER / IDEAHUB_AUTH_PASS 环境变量） ----
_AUTH_USER = os.environ.get("IDEAHUB_AUTH_USER", "")
_AUTH_PASS = os.environ.get("IDEAHUB_AUTH_PASS", "")
AUTH_ENABLED = bool(_AUTH_USER and _AUTH_PASS)

def _check_auth(request: Request) -> bool:
    if not AUTH_ENABLED:
        return True
    header = request.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        raw = base64.b64decode(header[6:]).decode("utf-8")
        user, _, pwd = raw.partition(":")
    except Exception:
        return False
    return hmac.compare_digest(user, _AUTH_USER) and hmac.compare_digest(pwd, _AUTH_PASS)

class TaskIn(BaseModel):
    title: str
    idea_summary: str = ""
    target_id: int
    hot_item_id: int | None = None
    feasibility_score: int
    score_breakdown: str = "{}"
    idea_path: str = ""
    notes: str = ""

class MoveIn(BaseModel):
    to_status: str

class PatchIn(BaseModel):
    title: str | None = None
    idea_summary: str | None = None
    feasibility_score: int | None = None
    score_breakdown: str | None = None
    notes: str | None = None

class TargetIn(BaseModel):
    name: str
    description: str = ""
    score_dimensions: str = "{}"

class SourceIn(BaseModel):
    type: str
    name: str
    url: str
    items_path: str = "data"
    title_field: str = "title"
    keywords: str = ""

class TagIn(BaseModel):
    name: str
    description: str = ""

class SettingIn(BaseModel):
    key: str
    value: str

def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="Idea Hub")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        if not _check_auth(request):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"},
                                headers={"WWW-Authenticate": 'Basic realm="Idea Hub"'})
        return await call_next(request)
    app.state.db_path = db_path

    @contextmanager
    def conn():
        c = db.connect(db_path)
        try:
            db.init_schema(c)
            yield c
        finally:
            c.close()

    @app.get("/api/stats")
    def stats(target_id: int | None = None):
        with conn() as c:
            return models.stats(c, target_id)

    @app.get("/api/hotspots")
    def hotspots(page: int = 1, page_size: int = 20):
        with conn() as c:
            try:
                return services.get_hotspot_summary(c, page, page_size)
            except ValueError as e:
                raise HTTPException(400, str(e))

    @app.get("/api/queues")
    def queues():
        with conn() as c:
            return services.get_queue_summary(c)

    @app.get("/api/queues/{status}")
    def queue_items(status: str, page: int = 1, page_size: int = 20):
        with conn() as c:
            try:
                return services.get_queue_items(c, status, page, page_size)
            except ValueError as e:
                raise HTTPException(400, str(e))

    @app.post("/api/generate")
    def generate():
        return services.generate_ideas(db_path, repo_root)

    @app.get("/api/tasks")
    def list_tasks(status: str | None = None, target_id: int | None = None):
        with conn() as c:
            items = models.list_tasks(c, status, target_id)
            for t in items:
                t["tags"] = models.list_task_tags(c, t["id"])
            return {"items": items}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int):
        with conn() as c:
            t = models.get_task(c, task_id)
            if not t: raise HTTPException(404, "task not found")
            if t["idea_path"] and pathlib.Path(t["idea_path"]).exists():
                t["idea_full"] = pathlib.Path(t["idea_path"]).read_text(encoding="utf-8")
            else:
                t["idea_full"] = ""
            t["tags"] = models.list_task_tags(c, task_id)
            return t

    @app.post("/api/tasks")
    def create_task(body: TaskIn):
        with conn() as c:
            tid = models.create_task(c, title=body.title, idea_summary=body.idea_summary,
                                     target_id=body.target_id, hot_item_id=body.hot_item_id,
                                     feasibility_score=body.feasibility_score,
                                     score_breakdown=body.score_breakdown,
                                     idea_path=body.idea_path, notes=body.notes)
            return models.get_task(c, tid)

    @app.delete("/api/tasks/{task_id}")
    def delete_task(task_id: int):
        """删除任务：级联清理关联表与产出文件。"""
        with conn() as c:
            t = models.get_task(c, task_id)
            if not t:
                raise HTTPException(404, "task not found")
            c.execute("DELETE FROM task_links WHERE task_id=?", (task_id,))
            c.execute("DELETE FROM task_tags WHERE task_id=?", (task_id,))
            c.execute("DELETE FROM execute_requests WHERE task_id=?", (task_id,))
            c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            c.commit()
        # 删除产出目录（idea.md / output.md 等）
        import shutil
        task_dir = pathlib.Path(t["idea_path"] or "").parent if t.get("idea_path") else None
        if task_dir and task_dir.exists():
            shutil.rmtree(task_dir, ignore_errors=True)
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/move")
    def move_task(task_id: int, body: MoveIn):
        with conn() as c:
            if not models.get_task(c, task_id): raise HTTPException(404, "task not found")
            try:
                models.move_task(c, task_id, body.to_status)
            except ValueError as e:
                raise HTTPException(400, str(e))
            return models.get_task(c, task_id)

    @app.patch("/api/tasks/{task_id}")
    def patch_task(task_id: int, body: PatchIn):
        with conn() as c:
            if not models.get_task(c, task_id): raise HTTPException(404, "task not found")
            models.update_task(c, task_id, **body.model_dump(exclude_none=True))
            return models.get_task(c, task_id)

    @app.post("/api/tasks/{task_id}/execute")
    def request_execution(task_id: int):
        with conn() as c:
            if not models.get_task(c, task_id): raise HTTPException(404, "task not found")
            c.execute("INSERT INTO execute_requests (task_id) VALUES (?)", (task_id,))
            c.commit()
            return {"ok": True}

    @app.post("/api/collect")
    def collect_now():
        """立即收集（含评分分流）。"""
        return services.collect_ideas(db_path, repo_root)

    @app.get("/api/targets")
    def list_targets():
        with conn() as c:
            return {"items": models.list_targets(c)}

    @app.post("/api/targets")
    def create_target(body: TargetIn):
        with conn() as c:
            tid = models.create_target(c, name=body.name, description=body.description,
                                       score_dimensions=body.score_dimensions)
            return {"id": tid}

    @app.post("/api/targets/{target_id}/activate")
    def activate_target(target_id: int):
        with conn() as c:
            t = next((t for t in models.list_targets(c) if t["id"] == target_id), None)
            if not t: raise HTTPException(404, "target not found")
            models.activate_target(c, target_id)
            return {"ok": True}

    @app.get("/api/sources")
    def list_sources():
        with conn() as c:
            return {"items": models.list_sources(c)}

    @app.post("/api/sources")
    def create_source(body: SourceIn):
        with conn() as c:
            sid = models.create_source(c, type=body.type, name=body.name, url=body.url,
                                        items_path=body.items_path, title_field=body.title_field,
                                        keywords=body.keywords)
            return {"id": sid}

    @app.post("/api/sources/{source_id}/toggle")
    def toggle_source(source_id: int):
        with conn() as c:
            src = next((s for s in models.list_sources(c) if s["id"] == source_id), None)
            if not src: raise HTTPException(404, "source not found")
            models.set_source_enabled(c, source_id, not src["enabled"])
            return {"ok": True}

    @app.delete("/api/sources/{source_id}")
    def delete_source(source_id: int):
        with conn() as c:
            src = next((s for s in models.list_sources(c) if s["id"] == source_id), None)
            if not src: raise HTTPException(404, "source not found")
            # 级联删除（FK 启用后按依赖顺序）：task_links → tasks.hot_item_id 清引用 → hot_items → sources，单事务
            c.execute("DELETE FROM task_links WHERE hot_item_id IN "
                      "(SELECT id FROM hot_items WHERE source_id=?)", (source_id,))
            c.execute("UPDATE tasks SET hot_item_id=NULL WHERE hot_item_id IN "
                      "(SELECT id FROM hot_items WHERE source_id=?)", (source_id,))
            c.execute("DELETE FROM hot_items WHERE source_id=?", (source_id,))
            c.execute("DELETE FROM sources WHERE id=?", (source_id,))
            c.commit()
            return {"ok": True}

    @app.get("/api/tags")
    def list_tags(active_only: bool = False):
        with conn() as c:
            return {"items": models.list_tags(c, active_only=active_only)}

    @app.post("/api/tags")
    def create_tag(body: TagIn):
        with conn() as c:
            tid = models.create_tag(c, name=body.name, description=body.description)
            return {"id": tid}

    @app.post("/api/tags/{tag_id}/toggle")
    def toggle_tag(tag_id: int):
        with conn() as c:
            tag = next((t for t in models.list_tags(c) if t["id"] == tag_id), None)
            if not tag:
                raise HTTPException(404, "tag not found")
            models.set_tag_active(c, tag_id, not tag["is_active"])
            return {"ok": True}

    @app.delete("/api/tags/{tag_id}")
    def delete_tag(tag_id: int):
        with conn() as c:
            models.delete_tag(c, tag_id)
            return {"ok": True}

    @app.post("/api/tasks/{task_id}/tags")
    def add_task_tag(task_id: int, body: TagIn):
        with conn() as c:
            if not models.get_task(c, task_id):
                raise HTTPException(404, "task not found")
            tag = next((t for t in models.list_tags(c) if t["name"] == body.name), None)
            if not tag:
                tag_id = models.create_tag(c, name=body.name)
            else:
                tag_id = tag["id"]
            models.add_task_tag(c, task_id, tag_id)
            return {"ok": True}

    @app.get("/api/settings")
    def get_settings():
        with conn() as c:
            return {"items": [{"key": k, "value": v} for k, v in
                              c.execute("SELECT key, value FROM settings").fetchall()]}

    @app.put("/api/settings")
    def put_setting(body: SettingIn):
        with conn() as c:
            models.set_setting(c, body.key, body.value)
            return {"ok": True}

    repo_root = pathlib.Path(__file__).parent.parent
    web_dir = repo_root / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=web_dir), name="static")
        outputs_dir = repo_root / "outputs"
        if outputs_dir.exists():
            app.mount("/outputs", StaticFiles(directory=outputs_dir), name="outputs")
        @app.get("/")
        def index():
            return FileResponse(web_dir / "index.html")

    return app

# 模块级 app 供 `uvicorn idea_hub.server:app` 导入（Task 8 cron 也依赖此入口）
app = create_app("data/idea.db")

def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)

if __name__ == "__main__":
    main()
