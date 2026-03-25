from __future__ import annotations

import urllib.error
import urllib.request

from fastapi import HTTPException
from fastapi.responses import Response
from sqlmodel import Session

from ..domains.paper_tags import extract_abstract_for_tagging
from ..domains.pdf_figures import extract_pdf_figures, extract_pdf_tables
from ..integrations.poe import classify_paper_tags
from ..integrations.semantic_scholar import safe_refresh_semantic_scholar_result
from ..persistence import crud


def extract_and_store_figures(
    session: Session,
    conversation_id: str,
    file_bytes: bytes,
    preferred_direction: str | None = None,
):
    try:
        extracted_figures = extract_pdf_figures(file_bytes, preferred_direction=preferred_direction)
        crud.replace_figures(session, conversation_id, extracted_figures)
        return crud.get_figures(session, conversation_id)
    except Exception as exc:
        print(f"Error extracting figures for conversation {conversation_id}: {exc}")
        session.rollback()
        return crud.get_figures(session, conversation_id)


def extract_and_store_tables(
    session: Session,
    conversation_id: str,
    file_bytes: bytes,
    preferred_direction: str | None = None,
):
    try:
        extracted_tables = extract_pdf_tables(file_bytes, preferred_direction=preferred_direction)
        crud.replace_tables(session, conversation_id, extracted_tables)
        return crud.get_tables(session, conversation_id)
    except Exception as exc:
        print(f"Error extracting tables for conversation {conversation_id}: {exc}")
        session.rollback()
        return crud.get_tables(session, conversation_id)


async def extract_and_store_tags(
    session: Session,
    conversation_id: str,
    title: str,
    first_bot_message: str,
    tag_model: str,
    api_key: str,
):
    abstract = extract_abstract_for_tagging(first_bot_message)
    if not title or not abstract:
        return crud.get_tags(session, conversation_id)

    try:
        extracted_tags = await classify_paper_tags(title, abstract, tag_model, api_key)
        if extracted_tags:
            crud.replace_tags(session, conversation_id, extracted_tags)
        return crud.get_tags(session, conversation_id)
    except Exception as exc:
        print(f"Error extracting tags for conversation {conversation_id}: {exc}")
        session.rollback()
        return crud.get_tags(session, conversation_id)


def refresh_conversation_semantic_result(
    session: Session,
    conversation_id: str,
    title: str,
):
    return safe_refresh_semantic_scholar_result(
        session=session,
        conversation_id=conversation_id,
        title=title,
    )


async def refresh_conversation_annotations(
    session: Session,
    conversation_id: str,
    title: str,
    first_bot_message: str,
    tag_model: str,
    api_key: str,
):
    tags = await extract_and_store_tags(
        session,
        conversation_id,
        title,
        first_bot_message,
        tag_model,
        api_key,
    )
    semantic_result = refresh_conversation_semantic_result(session, conversation_id, title)
    return tags, semantic_result


def build_asset_response(asset):
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    if asset.image_data is not None:
        return Response(content=bytes(asset.image_data), media_type=asset.image_mime_type or "image/webp")
    raise HTTPException(status_code=404, detail="Asset data not found.")


def download_pdf_bytes(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "translate-reprocess/1.0",
            "Accept": "application/pdf,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"Failed to download PDF from {url}: {exc}") from exc
