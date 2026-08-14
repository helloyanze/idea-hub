"""Task API routes."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import db
from ..services import tasks as tasks_service


router = APIRouter(prefix="/api/v1")


class TaskCreate(BaseModel):
    title: str
    content_type: str = "article"
    idea_summary: str = ""
    feasibility_score: int = 0
    score_breakdown: dict | None = None
    target_desc: str = ""
    notes: str = ""
    hotspot_id: int | None = None
    expire_at: str | None = None


class TaskPatch(BaseModel):
    title: str | None = None
    idea_summary: str | None = None
    ai_summary: str | None = None
    content_type: str | None = None
    feasibility_score: int | None = None
    score_breakdown: dict | None = None
    target_desc: str | None = None
    notes: str | None = None
    expire_at: str | None = None


class MoveIn(BaseModel):
    to_status: str


class RedoIn(BaseModel):
    note: str | None = None


class TaskTagsIn(BaseModel):
    names: list[str]


@router.get("/tasks")
def list_tasks(
    request: Request,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    q: str | None = None,
    tag: str | None = None,
):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {
            "data": tasks_service.list_tasks(
                conn,
                status=status,
                page=page,
                size=size,
                q=q,
                tag=tag,
            )
        }
    finally:
        conn.close()


@router.get("/tasks/{task_id}")
def get_task(request: Request, task_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": tasks_service.get_task(conn, task_id)}
    finally:
        conn.close()


@router.post("/tasks")
def create_task(request: Request, body: TaskCreate):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": tasks_service.create_task(conn, body.model_dump())}
    finally:
        conn.close()


@router.patch("/tasks/{task_id}")
def update_task(request: Request, task_id: int, body: TaskPatch):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {
            "data": tasks_service.update_task(
                conn,
                task_id,
                body.model_dump(exclude_unset=True),
            )
        }
    finally:
        conn.close()


@router.delete("/tasks/{task_id}")
def delete_task(request: Request, task_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        tasks_service.delete_task(
            conn,
            task_id,
            request.app.state.config.base_path,
        )
        return {"data": {"deleted": True}}
    finally:
        conn.close()


@router.post("/tasks/{task_id}/move")
def move_task(request: Request, task_id: int, body: MoveIn):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": tasks_service.move_task(conn, task_id, body.to_status)}
    finally:
        conn.close()


@router.post("/tasks/{task_id}/redo")
def redo_task(request: Request, task_id: int, body: RedoIn):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": tasks_service.redo_task(conn, task_id, body.note)}
    finally:
        conn.close()


@router.post("/tasks/{task_id}/reset-failures")
def reset_failures(request: Request, task_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": tasks_service.reset_failures(conn, task_id)}
    finally:
        conn.close()


@router.put("/tasks/{task_id}/tags")
def set_task_tags(request: Request, task_id: int, body: TaskTagsIn):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": tasks_service.set_task_tags(conn, task_id, body.names)}
    finally:
        conn.close()
