"""Hotspot API routes."""

from fastapi import APIRouter, Request

from .. import db
from ..services import hotspots as hotspots_service


router = APIRouter(prefix="/api/v1")


@router.get("/hotspots")
def list_hotspots(
    request: Request,
    page: int = 1,
    size: int = 20,
    source_id: int | None = None,
    verdict: str | None = None,
    q: str | None = None,
):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {
            "data": hotspots_service.list_hotspots(
                conn,
                page=page,
                size=size,
                source_id=source_id,
                verdict=verdict,
                q=q,
            )
        }
    finally:
        conn.close()


@router.get("/hotspots/{item_id}")
def get_hotspot(request: Request, item_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": hotspots_service.get_hotspot(conn, item_id)}
    finally:
        conn.close()
