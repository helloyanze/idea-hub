"""Notification API routes."""

from fastapi import APIRouter, Request

from .. import db
from ..services import notifications as notifications_service


router = APIRouter(prefix="/api/v1")


@router.get("/notifications")
def list_notifications(
    request: Request,
    page: int = 1,
    size: int = 20,
    level: str | None = None,
    type: str | None = None,
    unread_only: bool = False,
):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {
            "data": notifications_service.list_notifications(
                conn,
                page=page,
                size=size,
                level=level,
                type=type,
                unread_only=unread_only,
            )
        }
    finally:
        conn.close()


@router.get("/notifications/unread-count")
def unread_count(request: Request):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": {"count": notifications_service.unread_count(conn)}}
    finally:
        conn.close()


@router.post("/notifications/{notification_id}/read")
def mark_read(request: Request, notification_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {
            "data": notifications_service.mark_read(conn, notification_id)
        }
    finally:
        conn.close()


@router.post("/notifications/read-all")
def read_all(request: Request):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": notifications_service.read_all(conn)}
    finally:
        conn.close()
