"""Statistics API routes."""

from fastapi import APIRouter, Request

from .. import db
from ..services import stats as stats_service


router = APIRouter(prefix="/api/v1")


@router.get("/stats")
def get_stats(request: Request):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": stats_service.get_stats(conn)}
    finally:
        conn.close()


@router.get("/stats/trends")
def get_trends(request: Request, days: int = 7):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": stats_service.get_trends(conn, days=days)}
    finally:
        conn.close()
