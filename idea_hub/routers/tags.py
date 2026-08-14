"""Tag API routes."""

from fastapi import APIRouter, Request

from .. import db
from ..services import tags as tags_service


router = APIRouter(prefix="/api/v1")


@router.get("/tags")
def list_tags(request: Request):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": tags_service.list_tags(conn)}
    finally:
        conn.close()
