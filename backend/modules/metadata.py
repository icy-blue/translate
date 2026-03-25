from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlmodel import Session

from ..app.dependencies import check_read_only, get_api_key, get_db_session
from ..domain.message_payloads import infer_message_metadata
from ..domain.paper_tags import build_tag_payloads, extract_abstract_for_tagging
from ..platform.config import settings
from ..platform.gateways.poe import classify_paper_tags
from ..platform.gateways.semantic_scholar import safe_refresh_semantic_scholar_result
from .conversations import get_conversation, get_messages, get_tags, serialize_semantic_result, serialize_tags
from .search import normalize_tag_codes

router = APIRouter(tags=["metadata"])


def replace_tags(session: Session, conversation_id: str, tags: list[dict]) -> None:
    existing_tags = get_tags(session, conversation_id)
    for tag in existing_tags:
        session.delete(tag)
    from ..platform.models import PaperTag

    for tag in tags:
        session.add(
            PaperTag(
                conversation_id=conversation_id,
                category_code=tag["category_code"],
                category_label=tag["category_label"],
                tag_code=tag["tag_code"],
                tag_label=tag["tag_label"],
                tag_path=tag["tag_path"],
                source=tag.get("source", "poe"),
            )
        )
    session.commit()


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
        return get_tags(session, conversation_id)
    try:
        extracted_tags = await classify_paper_tags(title, abstract, tag_model, api_key)
        if extracted_tags:
            replace_tags(session, conversation_id, extracted_tags)
        return get_tags(session, conversation_id)
    except Exception as exc:
        print(f"Error extracting tags for conversation {conversation_id}: {exc}")
        session.rollback()
        return get_tags(session, conversation_id)


def refresh_conversation_semantic_result(session: Session, conversation_id: str, title: str):
    return safe_refresh_semantic_scholar_result(session=session, conversation_id=conversation_id, title=title)


async def refresh_conversation_metadata(session: Session, conversation_id: str, tag_model: str, api_key: str) -> dict:
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found.")
    messages = get_messages(session, conversation_id)
    first_bot_message = next((message.content for message in messages if infer_message_metadata(message)["role"] == "bot"), "")
    tags = await extract_and_store_tags(
        session=session,
        conversation_id=conversation_id,
        title=conversation.title or conversation.original_filename or "",
        first_bot_message=first_bot_message,
        tag_model=tag_model,
        api_key=api_key,
    )
    semantic_result = refresh_conversation_semantic_result(session, conversation_id, conversation.title or conversation.original_filename or "")
    semantic = serialize_semantic_result(semantic_result)
    return {"tags": [tag.model_dump() for tag in serialize_tags(tags)], **semantic.model_dump()}


def update_conversation_tags(session: Session, conversation_id: str, tag_codes: list[str]) -> dict:
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        raise ValueError("Conversation not found.")
    replace_tags(session, conversation_id, build_tag_payloads(tag_codes, source="manual"))
    return {"tags": [tag.model_dump() for tag in serialize_tags(get_tags(session, conversation_id))]}


@router.post("/metadata/{conversation_id}/refresh")
async def refresh_metadata_route(
    conversation_id: str,
    tag_model: str = Form(default=settings.poe_model),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    try:
        return await refresh_conversation_metadata(session, conversation_id, tag_model, api_key)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.put("/metadata/{conversation_id}/tags")
async def update_tags_route(
    conversation_id: str,
    tag_code: Optional[list[str]] = Form(default=None),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    try:
        return update_conversation_tags(session, conversation_id, normalize_tag_codes(tag_code))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
