"""Pipeline execution API routes."""

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from .. import db
from ..errors import AppError, BAD_REQUEST
from ..services import jobs as jobs_service


router = APIRouter(prefix="/api/v1")
_background_tasks: set = set()


class CollectIn(BaseModel):
    source_ids: list[int] | None = None


class GenerateIn(BaseModel):
    count: int | None = None
    hotspot_ids: list[int] | None = None


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


@router.post("/generate")
async def generate(request: Request, body: GenerateIn | None = None):
    config = request.app.state.config
    if not config.deepseek_api_key:
        raise AppError(
            status_code=400,
            code=BAD_REQUEST,
            message="DEEPSEEK_API_KEY 未配置：生成任务需要 LLM key，无法降级执行",
        )
    if body is not None and body.count is not None and body.count < 1:
        raise AppError(
            status_code=400,
            code=BAD_REQUEST,
            message="count 必须大于等于 1",
        )
    payload = {
        "count": body.count if body else None,
        "hotspot_ids": body.hotspot_ids if body else None,
    }
    conn = db.connect(config.db_path)
    try:
        existing = jobs_service.dedup_running(conn, "generate")
        if existing is not None:
            return {"data": {"job_id": existing, "reused": True}}
        job_id = jobs_service.create_job(conn, "generate", payload)
        jobs_service.mark_running(job_id)
    finally:
        conn.close()

    task = asyncio.create_task(
        asyncio.to_thread(
            jobs_service.run_generate_job,
            job_id,
            payload,
            config.db_path,
            config.deepseek_api_key,
            config.base_path,
        )
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"data": {"job_id": job_id, "reused": False}}
