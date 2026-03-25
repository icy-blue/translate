from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..domains.paper_tags import get_tag_definition
from ..persistence import crud
from ..persistence.models import Conversation, Message
from .message_utils import (
    normalize_document_outline_payload,
    normalize_translation_status_payload,
    safe_json_loads,
)

LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc


def ensure_local_timezone(dt: datetime) -> datetime:
    return dt.replace(tzinfo=LOCAL_TIMEZONE) if dt.tzinfo is None else dt.astimezone(LOCAL_TIMEZONE)


def serialize_message_record(message: Message) -> dict[str, Any]:
    payload = safe_json_loads(message.client_payload_json, {})
    translation_status = normalize_translation_status_payload(payload.get("translation_status")) if isinstance(payload, dict) else None
    document_outline = normalize_document_outline_payload(payload.get("document_outline")) if isinstance(payload, dict) else None
    return {
        "id": message.id,
        "conversation_id": message.conversation_id,
        "message_kind": message.message_kind,
        "section_category": message.section_category,
        "visible_to_user": message.visible_to_user,
        "content": message.content,
        "translation_status": translation_status,
        "document_outline": document_outline,
        "client_payload_json": message.client_payload_json,
        "created_at": message.created_at,
    }


def serialize_async_job(job) -> dict[str, Any]:
    payload = {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress or "",
        "conversation_id": job.conversation_id,
        "created_at": ensure_local_timezone(job.created_at),
        "started_at": ensure_local_timezone(job.started_at) if job.started_at else None,
        "finished_at": ensure_local_timezone(job.finished_at) if job.finished_at else None,
        "updated_at": ensure_local_timezone(job.updated_at),
    }
    if job.status == "succeeded":
        payload["result"] = safe_json_loads(job.result_json, {})
    if job.status == "failed":
        payload["error_message"] = job.error_message or "任务执行失败。"
    return payload


def serialize_figures(figures) -> list[dict[str, Any]]:
    return [
        {
            "id": figure.id,
            "page_number": figure.page_number,
            "figure_index": figure.figure_index,
            "figure_label": figure.figure_label,
            "caption": figure.caption,
            "image_url": f"/assets/figures/{figure.id}",
            "image_width": figure.image_width,
            "image_height": figure.image_height,
        }
        for figure in figures
    ]


def serialize_tables(tables) -> list[dict[str, Any]]:
    return [
        {
            "id": table.id,
            "page_number": table.page_number,
            "table_index": table.table_index,
            "table_label": table.table_label,
            "caption": table.caption,
            "image_url": f"/assets/tables/{table.id}",
            "image_width": table.image_width,
            "image_height": table.image_height,
        }
        for table in tables
    ]


def serialize_tags(tags) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for tag in tags:
        tag_definition = get_tag_definition(tag.tag_code)
        serialized.append(
            {
                "id": tag.id,
                "category_code": tag.category_code,
                "category_label": tag_definition.category_label if tag_definition else tag.category_label,
                "category_label_en": tag_definition.category_label_en if tag_definition else "",
                "tag_code": tag.tag_code,
                "tag_label": tag_definition.tag_label if tag_definition else tag.tag_label,
                "tag_label_en": tag_definition.tag_label_en if tag_definition else "",
                "tag_path": tag_definition.path if tag_definition else tag.tag_path,
                "tag_path_en": tag_definition.path_en if tag_definition else "",
                "source": tag.source,
            }
        )
    return serialized


def serialize_semantic_result(semantic_result) -> dict[str, Any]:
    if semantic_result is None:
        return {
            "venue_abbr": "",
            "ccf_category": "None",
            "ccf_type": "None",
            "citation_count": None,
            "venue": None,
            "year": None,
            "semantic_updated_at": None,
        }
    return {
        "venue_abbr": semantic_result.venue_abbr or "",
        "ccf_category": semantic_result.ccf_category or "None",
        "ccf_type": semantic_result.ccf_type or "None",
        "citation_count": semantic_result.citation_count,
        "venue": semantic_result.venue,
        "year": semantic_result.year,
        "semantic_updated_at": ensure_local_timezone(semantic_result.updated_at),
    }


def build_conversation_data_with_semantic(
    session,
    conversation: Conversation,
    semantic_result,
    include_relevance: bool = False,
    relevance_score: int = 0,
) -> dict[str, Any]:
    first_bot_msg = crud.get_first_bot_message(session, conversation.id)
    first_bot_content = first_bot_msg.content if first_bot_msg else ""
    summary = (first_bot_content[:200] + "...") if len(first_bot_content) > 200 else first_bot_content

    file_record = crud.get_file_record(session, conversation.id)
    pdf_url = file_record.poe_url if file_record else None
    tags = crud.get_tags(session, conversation.id)

    result = {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": ensure_local_timezone(conversation.created_at),
        "summary": summary,
        "pdf_url": pdf_url,
        "tags": serialize_tags(tags),
    }
    result.update(serialize_semantic_result(semantic_result))
    if include_relevance:
        result["relevance"] = relevance_score
    return result


def build_conversation_data(
    session,
    conversation: Conversation,
    include_relevance: bool = False,
    relevance_score: int = 0,
) -> dict[str, Any]:
    return build_conversation_data_with_semantic(
        session,
        conversation,
        crud.get_semantic_scholar_result(session, conversation.id),
        include_relevance,
        relevance_score,
    )


def build_conversations_data(
    session,
    conversations: list[Conversation],
    include_relevance: bool = False,
    relevance_scores: list[int] | None = None,
) -> list[dict[str, Any]]:
    relevance_scores = relevance_scores or ([0] * len(conversations))
    semantic_map = crud.get_semantic_scholar_results_map(session, [conv.id for conv in conversations])
    return [
        build_conversation_data_with_semantic(
            session,
            conv,
            semantic_map.get(conv.id),
            include_relevance,
            relevance_scores[i],
        )
        for i, conv in enumerate(conversations)
    ]
