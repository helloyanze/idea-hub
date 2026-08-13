"""Job status API routes."""

from fastapi import APIRouter, Request

from .. import db
from ..errors import AppError, BAD_REQUEST, NOT_FOUND


router = APIRouter(prefix="/api/v1")
_PAGE_SIZE = 20


@router.get("/jobs/{job_id}")
def get_job(request: Request, job_id: int):
    conn = db.connect(request.app.state.config.db_path)
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise AppError(
                status_code=404,
                code=NOT_FOUND,
                message=f"Job not found: {job_id}",
            )
        return {"data": dict(row)}
    finally:
        conn.close()


@router.get("/jobs")
def list_jobs(
    request: Request,
    type: str | None = None,
    status: str | None = None,
    page: int = 1,
):
    if page < 1:
        raise AppError(
            status_code=400,
            code=BAD_REQUEST,
            message="page must be >= 1",
        )

    conn = db.connect(request.app.state.config.db_path)
    try:
        where = []
        params = []
        if type is not None:
            where.append("type = ?")
            params.append(type)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        clause = " WHERE " + " AND ".join(where) if where else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM jobs{clause}", params
        ).fetchone()[0]
        offset = (page - 1) * _PAGE_SIZE
        rows = conn.execute(
            f"SELECT * FROM jobs{clause} ORDER BY id DESC LIMIT ? OFFSET ?",
            (*params, _PAGE_SIZE, offset),
        ).fetchall()
        return {
            "data": {
                "items": [dict(row) for row in rows],
                "total": total,
                "page": page,
                "page_size": _PAGE_SIZE,
            }
        }
    finally:
        conn.close()
