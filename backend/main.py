from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api.routers import (
    assets_router,
    conversations_router,
    jobs_router,
    search_router,
    system_router,
    upload_router,
)
from .core.database import engine
from .core.db_maintenance import (
    assert_message_schema_consistent,
    ensure_asset_columns,
    ensure_timestamp_timezone_columns,
)
from .persistence.models import SQLModel
from .services.async_jobs import recover_pending_jobs, start_job_workers, stop_job_workers

PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"


def create_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def on_startup():
        SQLModel.metadata.create_all(engine)
        ensure_asset_columns()
        assert_message_schema_consistent()
        ensure_timestamp_timezone_columns()
        recover_pending_jobs()
        start_job_workers()

    @app.on_event("shutdown")
    async def on_shutdown():
        await stop_job_workers()

    app.include_router(jobs_router)
    app.include_router(upload_router)
    app.include_router(assets_router)
    app.include_router(conversations_router)
    app.include_router(search_router)
    app.include_router(system_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()
_ensure_asset_columns = ensure_asset_columns
