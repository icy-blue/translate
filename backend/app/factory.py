from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .lifespan import app_lifespan
from ..modules.assets import router as assets_router
from ..modules.conversations import router as conversations_router
from ..modules.ingest import router as ingest_router
from ..modules.metadata import router as metadata_router
from ..modules.pipeline import router as pipeline_router
from ..modules.search import router as search_router
from ..modules.system import router as system_router
from ..modules.translation import router as translation_router
from ..platform.task_runtime import router as tasks_router

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "static"


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
