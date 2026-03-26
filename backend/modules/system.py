from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ..platform.config import settings

router = APIRouter(tags=["system"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = PROJECT_ROOT / "static"


@router.get("/")
async def serve_root():
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/chat/{path:path}")
async def serve_chat_paths(path: str):
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/chat")
async def serve_chat_root():
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/config")
async def get_config():
    return {"read_only": settings.read_only, "default_poe_model": settings.poe_model}
