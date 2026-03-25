from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc
from sqlmodel import Session, func, select

from ..app.dependencies import get_db_session
from ..domain.message_kinds import infer_message_kind, is_bot_message_kind
from ..domain.message_payloads import (
    normalize_document_outline_payload,
    normalize_translation_status_payload,
    preprocess_bot_reply_for_storage,
    safe_json_loads,
)
from ..domain.paper_tags import get_tag_definition
from ..platform.models import (
    Conversation,
    FileRecord,
    Message,
    PaperFigure,
    PaperSemanticScholarResult,
    PaperTable,
    PaperTag,
)

router = APIRouter(tags=["conversations"])
LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc


class MessageResponse(BaseModel):
    id: Optional[int] = None
    conversation_id: str
    message_kind: str
    section_category: Optional[str] = None
    visible_to_user: bool
    content: str
    translation_status: Optional[dict[str, Any]] = None
    document_outline: Optional[dict[str, Any]] = None
    client_payload_json: Optional[str] = None
    created_at: datetime


class FigureResponse(BaseModel):
    id: int
    page_number: int
    figure_index: int
    figure_label: Optional[str] = None
    caption: str
    image_url: str
    image_width: int
    image_height: int


class TableResponse(BaseModel):
    id: int
    page_number: int
    table_index: int
    table_label: Optional[str] = None
    caption: str
    image_url: str
    image_width: int
    image_height: int


class TagResponse(BaseModel):
    id: int
    category_code: str
    category_label: str
    category_label_en: str = ""
    tag_code: str
    tag_label: str
    tag_label_en: str = ""
    tag_path: str
    tag_path_en: str = ""
    source: str


class SemanticMetadataResponse(BaseModel):
    venue_abbr: str = ""
    ccf_category: str = "None"
    ccf_type: str = "None"
    citation_count: Optional[int] = None
    venue: Optional[str] = None
    year: Optional[int] = None
    semantic_updated_at: Optional[datetime] = None


class ConversationDetailResponse(SemanticMetadataResponse):
    id: str
    title: Optional[str] = None
    created_at: datetime
    messages: list[MessageResponse] = Field(default_factory=list)
    pdf_url: Optional[str] = None
    figures: list[FigureResponse] = Field(default_factory=list)
    tables: list[TableResponse] = Field(default_factory=list)
    tags: list[TagResponse] = Field(default_factory=list)


class ConversationListItemResponse(SemanticMetadataResponse):
    id: str
    title: Optional[str] = None
    created_at: datetime
    summary: str = ""
    pdf_url: Optional[str] = None
    tags: list[TagResponse] = Field(default_factory=list)
    relevance: Optional[int] = None


class ConversationListResponse(BaseModel):
    conversations: list[ConversationListItemResponse]
    has_more: bool
    total: int


def ensure_local_timezone(dt: datetime) -> datetime:
    return dt.replace(tzinfo=LOCAL_TIMEZONE) if dt.tzinfo is None else dt.astimezone(LOCAL_TIMEZONE)


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def normalize_message_payload_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return json_dumps(value)


def add_message(
    session: Session,
    *,
    conversation_id: str,
    content: str,
    message_kind: str,
    section_category: Optional[str] = None,
    visible_to_user: bool,
    client_payload: Any = None,
    created_at: Optional[datetime] = None,
) -> Message:
    normalized_message_kind = infer_message_kind(message_kind=message_kind, content=content)
    normalized_section_category = section_category if is_bot_message_kind(normalized_message_kind) else None
    normalized_content = content
    normalized_client_payload = client_payload
    if is_bot_message_kind(normalized_message_kind):
        prepared = preprocess_bot_reply_for_storage(content, client_payload)
        normalized_content = str(prepared["content"])
        normalized_client_payload = prepared["client_payload"]
    message = Message(
        conversation_id=conversation_id,
        message_kind=normalized_message_kind,
        section_category=normalized_section_category,
        visible_to_user=visible_to_user,
        content=normalized_content,
        client_payload_json=normalize_message_payload_json(normalized_client_payload),
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(message)
    return message


def create_message_pair(
    session: Session,
    conversation_id: str,
    user_content: str,
    bot_content: str,
    *,
    user_message_kind: str = "user_message",
    user_visible_to_user: bool = True,
    bot_section_category: Optional[str] = None,
    bot_client_payload: Any = None,
) -> None:
    add_message(
        session,
        conversation_id=conversation_id,
        content=user_content,
        message_kind=user_message_kind,
        visible_to_user=user_visible_to_user,
    )
    add_message(
        session,
        conversation_id=conversation_id,
        content=bot_content,
        message_kind="bot_reply",
        section_category=bot_section_category,
        visible_to_user=True,
        client_payload=bot_client_payload,
    )
    session.commit()


def get_conversation(session: Session, conversation_id: str) -> Optional[Conversation]:
    return session.get(Conversation, conversation_id)


def get_file_record(session: Session, conversation_id: str) -> Optional[FileRecord]:
    return session.exec(select(FileRecord).where(FileRecord.conversation_id == conversation_id)).first()


def get_messages(session: Session, conversation_id: str) -> list[Message]:
    return session.exec(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()


def get_first_bot_message(session: Session, conversation_id: str) -> Optional[Message]:
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.message_kind == "bot_reply")
        .order_by(Message.id)
    )
    return session.exec(statement).first()


def get_figures(session: Session, conversation_id: str) -> list[PaperFigure]:
    return session.exec(select(PaperFigure).where(PaperFigure.conversation_id == conversation_id).order_by(PaperFigure.figure_index)).all()


def get_tables(session: Session, conversation_id: str) -> list[PaperTable]:
    return session.exec(select(PaperTable).where(PaperTable.conversation_id == conversation_id).order_by(PaperTable.table_index)).all()


def get_tags(session: Session, conversation_id: str) -> list[PaperTag]:
    statement = select(PaperTag).where(PaperTag.conversation_id == conversation_id).order_by(PaperTag.category_code, PaperTag.tag_code)
    return session.exec(statement).all()


def get_semantic_result(session: Session, conversation_id: str) -> Optional[PaperSemanticScholarResult]:
    return session.exec(select(PaperSemanticScholarResult).where(PaperSemanticScholarResult.conversation_id == conversation_id)).first()


def get_semantic_results_map(session: Session, conversation_ids: list[str]) -> dict[str, PaperSemanticScholarResult]:
    if not conversation_ids:
        return {}
    statement = select(PaperSemanticScholarResult).where(PaperSemanticScholarResult.conversation_id.in_(conversation_ids))
    rows = session.exec(statement).all()
    return {row.conversation_id: row for row in rows}


def list_conversations(session: Session, offset: int, limit: int) -> list[Conversation]:
    statement = select(Conversation).order_by(desc(Conversation.created_at)).offset(offset).limit(limit)
    return session.exec(statement).all()


def count_conversations(session: Session) -> int:
    return session.exec(select(func.count(Conversation.id))).one()


def serialize_message(message: Message) -> MessageResponse:
    payload = safe_json_loads(message.client_payload_json, {})
    translation_status = normalize_translation_status_payload(payload.get("translation_status")) if isinstance(payload, dict) else None
    document_outline = normalize_document_outline_payload(payload.get("document_outline")) if isinstance(payload, dict) else None
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        message_kind=message.message_kind,
        section_category=message.section_category,
        visible_to_user=message.visible_to_user,
        content=message.content,
        translation_status=translation_status,
        document_outline=document_outline,
        client_payload_json=message.client_payload_json,
        created_at=ensure_local_timezone(message.created_at),
    )


def serialize_figures(figures) -> list[FigureResponse]:
    return [
        FigureResponse(
            id=figure.id,
            page_number=figure.page_number,
            figure_index=figure.figure_index,
            figure_label=figure.figure_label,
            caption=figure.caption,
            image_url=f"/assets/figures/{figure.id}",
            image_width=figure.image_width,
            image_height=figure.image_height,
        )
        for figure in figures
        if figure.id is not None
    ]


def serialize_tables(tables) -> list[TableResponse]:
    return [
        TableResponse(
            id=table.id,
            page_number=table.page_number,
            table_index=table.table_index,
            table_label=table.table_label,
            caption=table.caption,
            image_url=f"/assets/tables/{table.id}",
            image_width=table.image_width,
            image_height=table.image_height,
        )
        for table in tables
        if table.id is not None
    ]


def serialize_tags(tags) -> list[TagResponse]:
    serialized: list[TagResponse] = []
    for tag in tags:
        definition = get_tag_definition(tag.tag_code)
        serialized.append(
            TagResponse(
                id=tag.id,
                category_code=tag.category_code,
                category_label=definition.category_label if definition else tag.category_label,
                category_label_en=definition.category_label_en if definition else "",
                tag_code=tag.tag_code,
                tag_label=definition.tag_label if definition else tag.tag_label,
                tag_label_en=definition.tag_label_en if definition else "",
                tag_path=definition.path if definition else tag.tag_path,
                tag_path_en=definition.path_en if definition else "",
                source=tag.source,
            )
        )
    return serialized


def serialize_semantic_result(semantic_result) -> SemanticMetadataResponse:
    if semantic_result is None:
        return SemanticMetadataResponse()
    return SemanticMetadataResponse(
        venue_abbr=semantic_result.venue_abbr or "",
        ccf_category=semantic_result.ccf_category or "None",
        ccf_type=semantic_result.ccf_type or "None",
        citation_count=semantic_result.citation_count,
        venue=semantic_result.venue,
        year=semantic_result.year,
        semantic_updated_at=ensure_local_timezone(semantic_result.updated_at),
    )


def build_conversation_detail(session: Session, conversation_id: str) -> ConversationDetailResponse:
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    messages = get_messages(session, conversation_id)
    file_record = get_file_record(session, conversation_id)
    figures = get_figures(session, conversation_id)
    tables = get_tables(session, conversation_id)
    tags = get_tags(session, conversation_id)
    semantic = serialize_semantic_result(get_semantic_result(session, conversation_id))
    return ConversationDetailResponse(
        id=conversation.id,
        title=conversation.title,
        created_at=ensure_local_timezone(conversation.created_at),
        messages=[serialize_message(message) for message in messages],
        pdf_url=file_record.poe_url if file_record else None,
        figures=serialize_figures(figures),
        tables=serialize_tables(tables),
        tags=serialize_tags(tags),
        **semantic.model_dump(),
    )


def build_conversation_list_item(
    session: Session,
    conversation: Conversation,
    semantic_result,
    include_relevance: bool = False,
    relevance_score: int = 0,
) -> ConversationListItemResponse:
    first_bot_msg = get_first_bot_message(session, conversation.id)
    first_bot_content = first_bot_msg.content if first_bot_msg else ""
    summary = (first_bot_content[:200] + "...") if len(first_bot_content) > 200 else first_bot_content
    file_record = get_file_record(session, conversation.id)
    semantic = serialize_semantic_result(semantic_result)
    payload = {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": ensure_local_timezone(conversation.created_at),
        "summary": summary,
        "pdf_url": file_record.poe_url if file_record else None,
        "tags": serialize_tags(get_tags(session, conversation.id)),
        **semantic.model_dump(),
    }
    if include_relevance:
        payload["relevance"] = relevance_score
    return ConversationListItemResponse(**payload)


def build_conversation_list_items(
    session: Session,
    conversations: list[Conversation],
    include_relevance: bool = False,
    relevance_scores: Optional[list[int]] = None,
) -> list[ConversationListItemResponse]:
    relevance_scores = relevance_scores or ([0] * len(conversations))
    semantic_map = get_semantic_results_map(session, [conversation.id for conversation in conversations])
    return [
        build_conversation_list_item(session, conversation, semantic_map.get(conversation.id), include_relevance, relevance_scores[index])
        for index, conversation in enumerate(conversations)
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation_route(conversation_id: str, session: Session = Depends(get_db_session)):
    return build_conversation_detail(session, conversation_id).model_dump()


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations_route(
    limit: int = 10,
    offset: int = 0,
    tag_code: Optional[list[str]] = Query(default=None),
    ccf_category: Optional[list[str]] = Query(default=None),
    venue_filter: Optional[list[str]] = Query(default=None),
    year: Optional[list[str]] = Query(default=None),
    session: Session = Depends(get_db_session),
):
    from .search import list_conversations_payload

    return list_conversations_payload(
        session=session,
        limit=limit,
        offset=offset,
        tag_code=tag_code,
        ccf_category=ccf_category,
        venue_filter=venue_filter,
        year=year,
    )
