"""FastAPI application factory for Idea Hub."""

from collections import defaultdict, deque
from datetime import datetime, timedelta
import secrets
import time
from typing import Deque

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.middleware.cors import CORSMiddleware

from . import db
from .config import Config
from .errors import AppError, INTERNAL, RATE_LIMITED
from .routers.settings import router as settings_router


_basic = HTTPBasic(auto_error=False)


def require_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
) -> None:
    """Require configured HTTP Basic credentials, or allow anonymous access."""
    config: Config = request.app.state.config
    if not config.auth_user and not config.auth_pass:
        return
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Basic"},
        )
    valid_user = secrets.compare_digest(credentials.username, config.auth_user)
    valid_pass = secrets.compare_digest(credentials.password, config.auth_pass)
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


def scheduler_status(last_tick: datetime | None) -> tuple[str, datetime | None]:
    """Return scheduler health based on the age of its most recent tick."""
    if last_tick is None:
        return ("never_run", None)
    now = datetime.now(last_tick.tzinfo) if last_tick.tzinfo else datetime.now()
    if now - last_tick > timedelta(minutes=10):
        return ("unhealthy", last_tick)
    return ("ok", last_tick)


def create_app(config: Config) -> FastAPI:
    """Create and configure the Idea Hub FastAPI application."""
    app = FastAPI()
    app.state.config = config

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        limit = config.rate_limit_per_min
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        timestamps: Deque[float] = request.app.state.rate_limit_timestamps[client]
        cutoff = now - 60.0
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()
        if len(timestamps) >= limit:
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": RATE_LIMITED,
                        "message": "Rate limit exceeded",
                    }
                },
            )
        timestamps.append(now)
        try:
            return await call_next(request)
        except Exception:
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": INTERNAL,
                        "message": "Internal server error",
                    }
                },
            )

    app.state.rate_limit_timestamps = defaultdict(deque)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"error": {"code": INTERNAL, "message": "Internal server error"}},
        )

    router = APIRouter(prefix="/api/v1")

    @router.get("/health", dependencies=[Depends(require_auth)])
    def health():
        db_status = "ok"
        connection = None
        try:
            connection = db.connect(config.db_path)
            connection.execute("SELECT 1").fetchone()
        except Exception:
            db_status = "error"
        finally:
            if connection is not None:
                connection.close()
        scheduler_state, last_tick = scheduler_status(None)
        return {
            "data": {
                "status": "ok",
                "db": db_status,
                "scheduler": {"last_tick": last_tick, "status": scheduler_state},
            }
        }

    app.include_router(router)
    app.include_router(settings_router, dependencies=[Depends(require_auth)])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=getattr(config, "cors_origins", ["http://localhost:5173"]),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )
    return app
