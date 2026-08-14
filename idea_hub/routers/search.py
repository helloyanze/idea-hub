"""Unified search API routes."""

from fastapi import APIRouter, Request

from .. import db
from ..services import search as search_service


router = APIRouter(prefix="/api/v1")


@router.get("/search")
def search(request: Request, q: str, page: int = 1, size: int = 20):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {
            "data": search_service.search(
                conn,
                q=q,
                page=page,
                size=size,
            )
        }
    finally:
        conn.close()
