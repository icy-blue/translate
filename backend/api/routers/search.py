from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from ...core.dependencies import get_db_session
from ...domains.paper_tags import get_tag_library_payload
from ...services.search import (
    build_search_filter_payload,
    get_tag_usage_counts,
    search_conversation_payload,
)

router = APIRouter()


@router.get("/search")
async def search_conversations(
    q: str = "",
    search_type: str = "all",
    tag_code: Optional[list[str]] = Query(default=None),
    ccf_category: Optional[list[str]] = Query(default=None),
    venue_filter: Optional[list[str]] = Query(default=None),
    year: Optional[list[str]] = Query(default=None),
    session: Session = Depends(get_db_session),
):
    return search_conversation_payload(
        session,
        q=q,
        search_type=search_type,
        tag_code=tag_code,
        ccf_category=ccf_category,
        venue_filter=venue_filter,
        year=year,
    )


@router.get("/tags/library")
async def get_tag_library(session: Session = Depends(get_db_session)):
    return {"categories": get_tag_library_payload(get_tag_usage_counts(session))}


@router.get("/search/filters")
async def get_search_filters(session: Session = Depends(get_db_session)):
    return build_search_filter_payload(session)
