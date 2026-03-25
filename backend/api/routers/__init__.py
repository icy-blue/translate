from .assets import router as assets_router
from .conversations import router as conversations_router
from .jobs import router as jobs_router
from .search import router as search_router
from .system import router as system_router
from .upload import router as upload_router

__all__ = [
    "assets_router",
    "conversations_router",
    "jobs_router",
    "search_router",
    "system_router",
    "upload_router",
]
