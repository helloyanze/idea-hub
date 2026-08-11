import json, pathlib
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from idea_hub import db, models

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

class SettingIn(BaseModel):
    key: str
    value: str

def create_app(db_path: str) -> FastAPI:
    app = FastAPI(title="Idea Hub")
    app.state.db_path = db_path

    def conn():
        c = db.connect(db_path); db.init_schema(c); return c

    @app.get("/api/stats")
    def stats(target_id: int | None = None):
        with conn() as c:
            return models.stats(c, target_id)

    @app.get("/api/tasks")
    def list_tasks(status: str | None = None, target_id: int | None = None):
        with conn() as c:
            return {"items": models.list_tasks(c, status, target_id)}

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: int):
        with conn() as c:
            t = models.get_task(c, task_id)
            if not t: raise HTTPException(404, "task not found")
            if t["idea_path"] and pathlib.Path(t["idea_path"]).exists():
                t["idea_full"] = pathlib.Path(t["idea_path"]).read_text(encoding="utf-8")
            else:
                t["idea_full"] = ""
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

    @app.post("/api/tasks/{task_id}/move")
    def move_task(task_id: int, body: MoveIn):
        with conn() as c:
            if not models.get_task(c, task_id): raise HTTPException(404, "task not found")
            models.move_task(c, task_id, body.to_status)
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
            models.activate_target(c, target_id)
            return {"ok": True}

    @app.get("/api/sources")
    def list_sources():
        with conn() as c:
            return {"items": models.list_sources(c)}

    @app.post("/api/sources")
    def create_source(body: SourceIn):
        with conn() as c:
            sid = models.create_source(c, type=body.type, name=body.name, url=body.url)
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
            c.execute("DELETE FROM sources WHERE id=?", (source_id,)); c.commit()
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
