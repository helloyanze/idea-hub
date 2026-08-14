"""Task output API routes."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import db
from ..services import outputs as outputs_service


router = APIRouter(prefix="/api/v1")


class OutputPut(BaseModel):
    content: str
    ai_summary: str | None = None
    base_version: int | None = None


class OutputUpload(BaseModel):
    filename: str
    content: str


@router.get("/tasks/{task_id}/output")
def get_latest_output(request: Request, task_id: int):
    config = request.app.state.config
    conn = db.connect(config.db_path)
    try:
        return {
            "data": outputs_service.get_latest(
                conn,
                task_id,
                config.base_path,
            )
        }
    finally:
        conn.close()


@router.put("/tasks/{task_id}/output")
def save_output(request: Request, task_id: int, body: OutputPut):
    config = request.app.state.config
    conn = db.connect(config.db_path)
    try:
        return {
            "data": outputs_service.save(
                conn,
                task_id,
                body.content,
                config.base_path,
                base_version=body.base_version,
                ai_summary=body.ai_summary,
            )
        }
    finally:
        conn.close()


@router.post("/tasks/{task_id}/output/upload")
def upload_output(request: Request, task_id: int, body: OutputUpload):
    config = request.app.state.config
    conn = db.connect(config.db_path)
    try:
        return {
            "data": outputs_service.upload(
                conn,
                task_id,
                body.filename,
                body.content,
                config.base_path,
            )
        }
    finally:
        conn.close()


@router.get("/tasks/{task_id}/output/versions")
def list_output_versions(request: Request, task_id: int):
    config = request.app.state.config
    conn = db.connect(config.db_path)
    try:
        return {"data": outputs_service.list_versions(conn, task_id)}
    finally:
        conn.close()


@router.get("/tasks/{task_id}/output/versions/{version}")
def get_output_version(request: Request, task_id: int, version: int):
    config = request.app.state.config
    conn = db.connect(config.db_path)
    try:
        return {
            "data": outputs_service.get_version(
                conn,
                task_id,
                version,
            )
        }
    finally:
        conn.close()
