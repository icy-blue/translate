from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlmodel import Session

from ...core.config import settings
from ...core.dependencies import check_read_only, get_api_key, get_agent_ingest_token, get_db_session
from ...domains.paper_tags import build_tag_payloads
from ...persistence import crud
from ...persistence.models import Conversation
from ...schemas.pipeline import PipelineBundlePayload
from ...services.annotations import refresh_conversation_annotations
from ...services.async_jobs import enqueue_async_job, get_session_enqueue_lock
from ...services.message_utils import infer_message_metadata
from ...services.search import (
    build_filtered_conversation_statement,
    count_filtered_conversations,
    normalize_string_filters,
    normalize_tag_codes,
    normalize_year_filters,
)
from ...services.serializers import (
    build_conversations_data,
    serialize_figures,
    serialize_message_record,
    serialize_semantic_result,
    serialize_tables,
    serialize_tags,
)

router = APIRouter()


@router.post("/continue/{conversation_id}")
async def continue_translation(
    conversation_id: str,
    poe_model: str = Form(default=settings.poe_model),
    auto_translate_appendix: bool = Form(default=False),
    auto_translate_acknowledgements: bool = Form(default=False),
    auto_translate_references: bool = Form(default=False),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    enqueue_lock = await get_session_enqueue_lock(conversation_id)
    async with enqueue_lock:
        active_job = crud.get_active_translation_job(session, conversation_id)
        if active_job:
            raise HTTPException(
                status_code=409,
                detail=f"会话已有翻译任务进行中（job_id={active_job.id}，状态={active_job.status}）。请等待完成后再继续。",
            )
        return enqueue_async_job(
            "continue",
            {
                "conversation_id": conversation_id,
                "new_user_message": "继续",
                "poe_model": poe_model,
                "api_key": api_key,
                "save_to_record": True,
                "auto_translate_appendix": auto_translate_appendix,
                "auto_translate_acknowledgements": auto_translate_acknowledgements,
                "auto_translate_references": auto_translate_references,
            },
            conversation_id=conversation_id,
        )


@router.post("/custom_message/{conversation_id}")
async def custom_message(
    conversation_id: str,
    message: str = Form(...),
    save_to_record: bool = Form(...),
    poe_model: str = Form(default=settings.poe_model),
    auto_translate_appendix: bool = Form(default=False),
    auto_translate_acknowledgements: bool = Form(default=False),
    auto_translate_references: bool = Form(default=False),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    if not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    enqueue_lock = await get_session_enqueue_lock(conversation_id)
    async with enqueue_lock:
        active_job = crud.get_active_translation_job(session, conversation_id)
        if active_job:
            raise HTTPException(
                status_code=409,
                detail=f"会话已有翻译任务进行中（job_id={active_job.id}，状态={active_job.status}）。请等待完成后再发送新消息。",
            )
        return enqueue_async_job(
            "custom_message",
            {
                "conversation_id": conversation_id,
                "new_user_message": message,
                "poe_model": poe_model,
                "api_key": api_key,
                "save_to_record": save_to_record,
                "auto_translate_appendix": auto_translate_appendix,
                "auto_translate_acknowledgements": auto_translate_acknowledgements,
                "auto_translate_references": auto_translate_references,
            },
            conversation_id=conversation_id,
        )


@router.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str, session: Session = Depends(get_db_session)):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    messages = crud.get_messages(session, conversation_id)
    file_record = crud.get_file_record(session, conversation_id)
    figures = crud.get_figures(session, conversation_id)
    tables = crud.get_tables(session, conversation_id)
    tags = crud.get_tags(session, conversation_id)
    semantic_result = crud.get_semantic_scholar_result(session, conversation_id)
    pdf_url = file_record.poe_url if file_record else None

    response = {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at,
        "messages": [serialize_message_record(message) for message in messages],
        "pdf_url": pdf_url,
        "figures": serialize_figures(figures),
        "tables": serialize_tables(tables),
        "tags": serialize_tags(tags),
    }
    response.update(serialize_semantic_result(semantic_result))
    return response


@router.get("/conversations")
async def list_conversations(
    limit: int = 10,
    offset: int = 0,
    tag_code: Optional[list[str]] = Query(default=None),
    ccf_category: Optional[list[str]] = Query(default=None),
    venue_filter: Optional[list[str]] = Query(default=None),
    year: Optional[list[str]] = Query(default=None),
    session: Session = Depends(get_db_session),
):
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    normalized_tag_codes = normalize_tag_codes(tag_code)
    normalized_ccf_categories = normalize_string_filters(ccf_category)
    normalized_venue_filters = normalize_string_filters(venue_filter)
    normalized_years = normalize_year_filters(year)

    total = count_filtered_conversations(
        session,
        tag_codes=normalized_tag_codes,
        ccf_categories=normalized_ccf_categories,
        venue_filters=normalized_venue_filters,
        years=normalized_years,
    )
    conversations_statement = build_filtered_conversation_statement(
        tag_codes=normalized_tag_codes,
        ccf_categories=normalized_ccf_categories,
        venue_filters=normalized_venue_filters,
        years=normalized_years,
    ).order_by(Conversation.created_at.desc())
    conversations = session.exec(conversations_statement.offset(offset).limit(limit + 1)).all()

    has_more = len(conversations) > limit
    conversations = conversations[:limit]

    result = build_conversations_data(session, conversations)
    return {"conversations": result, "has_more": has_more, "total": total}


@router.post("/conversation/{conversation_id}/refresh_metadata")
async def refresh_conversation_metadata(
    conversation_id: str,
    tag_model: str = Form(default=settings.poe_model),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    messages = crud.get_messages(session, conversation_id)
    first_bot_message = next((message.content for message in messages if infer_message_metadata(message)["role"] == "bot"), "")
    tags, semantic_result = await refresh_conversation_annotations(
        session=session,
        conversation_id=conversation_id,
        title=conversation.title or conversation.original_filename or "",
        first_bot_message=first_bot_message,
        tag_model=tag_model,
        api_key=api_key,
    )
    response = {"tags": serialize_tags(tags)}
    response.update(serialize_semantic_result(semantic_result))
    return response


@router.post("/conversation/{conversation_id}/tags")
async def update_conversation_tags(
    conversation_id: str,
    tag_code: Optional[list[str]] = Form(default=None),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    normalized_tag_codes = normalize_tag_codes(tag_code)
    crud.replace_tags(session, conversation_id, build_tag_payloads(normalized_tag_codes, source="manual"))
    return {"tags": serialize_tags(crud.get_tags(session, conversation_id))}


@router.post("/agent/pipeline/commit")
async def commit_agent_pipeline_bundle(
    payload: PipelineBundlePayload,
    _agent_token: str = Depends(get_agent_ingest_token),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    try:
        return crud.persist_pipeline_bundle(session, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist pipeline bundle: {exc}")
