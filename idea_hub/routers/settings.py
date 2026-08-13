"""Settings API routes."""

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import db
from ..services import settings as settings_service


router = APIRouter(prefix="/api/v1")


class SettingIn(BaseModel):
    key: str
    value: Any


@router.get("/settings")
def get_settings(request: Request):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": settings_service.get_all(conn)}
    finally:
        conn.close()


@router.put("/settings")
def put_settings(request: Request, body: SettingIn):
    conn = db.connect(request.app.state.config.db_path)
    try:
        return {"data": settings_service.update(conn, body.key, body.value)}
    finally:
        conn.close()
