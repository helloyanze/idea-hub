"""Pipeline execution API routes."""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
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

class ExecuteIn(BaseModel):
    task_ids: list[int]


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

@router.post("/execute")
async def execute(request: Request, body: ExecuteIn | None = None):
    config = request.app.state.config
    if not config.deepseek_api_key:
        raise AppError(
            status_code=400,
            code=BAD_REQUEST,
            message="DEEPSEEK_API_KEY 未配置：执行任务需要 LLM key，无法降级执行",
        )
    if body is None or not body.task_ids:
        raise AppError(
            status_code=400,
            code=BAD_REQUEST,
            message="task_ids 必填且不能为空",
        )
    task_ids = body.task_ids
    conn = db.connect(config.db_path)
    try:
        # 同步校验全部任务前置：不存在或 in_progress 非法，任一非法整体 409
        invalid = []
        seen = set()
        for task_id in task_ids:
            if task_id in seen:
                continue
            seen.add(task_id)
            row = conn.execute(
                "SELECT status FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None or row["status"] == "in_progress":
                invalid.append(task_id)
        if invalid:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "code": "INVALID_TASK_IDS",
                        "message": f"Invalid task ids: {invalid}",
                    },
                    "invalid_task_ids": invalid,
                },
            )
        existing = jobs_service.dedup_running(conn, "execute")
        if existing is not None:
            return {"data": {"job_id": existing, "reused": True}}
        payload = {"task_ids": task_ids}
        job_id = jobs_service.create_job(conn, "execute", payload)
        jobs_service.mark_running(job_id)
    finally:
        conn.close()

    task = asyncio.create_task(
        asyncio.to_thread(
            jobs_service.run_execute_job,
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
