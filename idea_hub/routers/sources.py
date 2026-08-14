"""Source configuration API routes."""

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import db
from ..services import sources as sources_service


router = APIRouter(prefix="/api/v1")


class SourceIn(BaseModel):
    type: str
    name: str
    url: str
    enabled: bool = True
    items_path: str | None = None
    title_field: str | None = None
    keywords: str = ""
    ttl_hours: int | None = 24
    channel_config: dict = {}


class SourcePatch(BaseModel):
    type: str | None = None
    name: str | None = None
    url: str | None = None
    enabled: bool | None = None
    items_path: str | None = None
    title_field: str | None = None
    keywords: str | None = None
    ttl_hours: int | None = None
    channel_config: dict | None = None


@router.get("/sources")
def list_sources(request: Request):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": sources_service.list_sources(conn)}
    finally:
        conn.close()


@router.post("/sources")
def create_source(request: Request, body: SourceIn):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": sources_service.create_source(conn, body.model_dump())}
    finally:
        conn.close()


@router.patch("/sources/{source_id}")
def update_source(request: Request, source_id: int, body: SourcePatch):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": sources_service.update_source(conn, source_id, body.model_dump())}
    finally:
        conn.close()


@router.post("/sources/{source_id}/toggle")
def toggle_source(request: Request, source_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": sources_service.toggle_source(conn, source_id)}
    finally:
        conn.close()


@router.delete("/sources/{source_id}")
def delete_source(request: Request, source_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        sources_service.delete_source(conn, source_id)
        return {"data": {"deleted": True}}
    finally:
        conn.close()


@router.post("/sources/{source_id}/test")
def test_source(request: Request, source_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": sources_service.test_source(conn, source_id)}
    finally:
        conn.close()
