from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel

from ..modules.assets import router as assets_router
from ..modules.conversations import router as conversations_router
from ..modules.ingest import router as ingest_router
from ..modules.metadata import router as metadata_router
from ..modules.pipeline import router as pipeline_router
from ..modules.search import router as search_router
from ..modules.system import router as system_router
from ..modules.translation import router as translation_router
from ..platform.config import engine
from ..platform.models import register_sqlmodel_tables
from ..platform.schema_maintenance import (
    assert_message_schema_consistent,
    ensure_asset_columns,
    ensure_timestamp_timezone_columns,
)
from ..platform.task_runtime import router as tasks_router
from ..platform.task_runtime import recover_pending_tasks, start_task_workers, stop_task_workers

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "static"


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    register_sqlmodel_tables()
    SQLModel.metadata.create_all(engine)
    ensure_asset_columns()
    assert_message_schema_consistent()
    ensure_timestamp_timezone_columns()
    recover_pending_tasks()
    start_task_workers()
    try:
        yield
    finally:
        await stop_task_workers()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=app_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(tasks_router)
    app.include_router(ingest_router)
    app.include_router(translation_router)
    app.include_router(conversations_router)
    app.include_router(metadata_router)
    app.include_router(assets_router)
    app.include_router(search_router)
    app.include_router(pipeline_router)
    app.include_router(system_router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    return app


app = create_app()
