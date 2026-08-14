"""Pipeline execution API routes."""

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import db
from ..services import jobs as jobs_service


router = APIRouter(prefix="/api/v1")
_background_tasks: set = set()


class CollectIn(BaseModel):
    source_ids: list[int] | None = None


@router.post("/collect")
async def collect(request: Request, body: CollectIn | None = None):
    config = request.app.state.config
    payload = {"source_ids": body.source_ids if body else None}
    conn = db.connect(config.db_path)
    try:
        existing = jobs_service.dedup_running(conn, "collect")
        if existing is not None:
            return {"data": {"job_id": existing, "reused": True}}
        job_id = jobs_service.create_job(conn, "collect", payload)
        jobs_service.mark_running(job_id)
    finally:
        conn.close()

    task = asyncio.create_task(
        asyncio.to_thread(
            jobs_service.run_collect_job,
            job_id,
            payload,
            config.db_path,
            config.deepseek_api_key,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"data": {"job_id": job_id, "reused": False}}
