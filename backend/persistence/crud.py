from __future__ import annotations

import base64
import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlmodel import Session, select, func
from sqlalchemy import desc

from .models import (
    Conversation,
    Message,
    FileRecord,
    PaperFigure,
    PaperTable,
    PaperTag,
    PaperSemanticScholarResult,
    AsyncJob,
)
from ..domains.message_kinds import infer_message_kind, is_bot_message_kind
from ..services.message_utils import preprocess_bot_reply_for_storage


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _normalize_message_payload_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return _json_dumps(value)


def add_message(
    session: Session,
    *,
    conversation_id: str,
    content: str,
    message_kind: str,
    section_category: str | None = None,
    visible_to_user: bool,
    client_payload: Any = None,
    created_at: datetime | None = None,
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
        client_payload_json=_normalize_message_payload_json(normalized_client_payload),
        created_at=created_at or datetime.now(timezone.utc),
    )
    session.add(message)
    return message

def get_conversation(session: Session, conversation_id: str) -> Conversation | None:
    """Fetch a conversation by its ID."""
    return session.get(Conversation, conversation_id)

def get_file_record(session: Session, conversation_id: str) -> FileRecord | None:
    """Fetch a file record by conversation ID."""
    return session.exec(
        select(FileRecord).where(FileRecord.conversation_id == conversation_id)
    ).first()

def get_messages(session: Session, conversation_id: str) -> list[Message]:
    """Fetch all messages for a conversation, ordered by creation."""
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return session.exec(statement).all()


def get_first_bot_message(session: Session, conversation_id: str) -> Message | None:
    """Fetch the first bot message for a conversation."""
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.message_kind == "bot_reply")
        .order_by(Message.id)
    )
    return session.exec(statement).first()


def get_figures(session: Session, conversation_id: str) -> list[PaperFigure]:
    """Fetch all extracted figures for a conversation."""
    statement = (
        select(PaperFigure)
        .where(PaperFigure.conversation_id == conversation_id)
        .order_by(PaperFigure.figure_index)
    )
    return session.exec(statement).all()


def get_tables(session: Session, conversation_id: str) -> list[PaperTable]:
    """Fetch all extracted tables for a conversation."""
    statement = (
        select(PaperTable)
        .where(PaperTable.conversation_id == conversation_id)
        .order_by(PaperTable.table_index)
    )
    return session.exec(statement).all()


def get_tags(session: Session, conversation_id: str) -> list[PaperTag]:
    """Fetch all extracted paper tags for a conversation."""
    statement = (
        select(PaperTag)
        .where(PaperTag.conversation_id == conversation_id)
        .order_by(PaperTag.category_code, PaperTag.tag_code)
    )
    return session.exec(statement).all()


def get_semantic_scholar_result(
    session: Session, conversation_id: str
) -> PaperSemanticScholarResult | None:
    statement = (
        select(PaperSemanticScholarResult)
        .where(PaperSemanticScholarResult.conversation_id == conversation_id)
    )
    return session.exec(statement).first()


def get_semantic_scholar_results_map(
    session: Session, conversation_ids: list[str]
) -> dict[str, PaperSemanticScholarResult]:
    if not conversation_ids:
        return {}
    statement = (
        select(PaperSemanticScholarResult)
        .where(PaperSemanticScholarResult.conversation_id.in_(conversation_ids))
    )
    rows = session.exec(statement).all()
    return {row.conversation_id: row for row in rows}

def find_existing_file(session: Session, fingerprint: str) -> FileRecord | None:
    """Find an existing file by its SHA256 fingerprint."""
    return session.exec(
        select(FileRecord).where(FileRecord.fingerprint == fingerprint)
    ).first()

def create_conversation_package(
    session: Session,
    conversation_id: str,
    file_id: str,
    title: str,
    original_filename: str,
    fingerprint: str,
    attachment: dict,
    initial_prompt: str,
    response_text: str,
    bot_client_payload: Any = None,
) -> None:
    """Create all initial database records for a new translation."""
    session.add(Conversation(
        id=conversation_id,
        title=title,
        original_filename=original_filename
    ))
    session.add(FileRecord(
        id=file_id,
        conversation_id=conversation_id,
        filename=original_filename,
        fingerprint=fingerprint,
        poe_url=attachment.url,
        content_type=attachment.content_type,
        poe_name=attachment.name
    ))
    add_message(
        session,
        conversation_id=conversation_id,
        content=initial_prompt,
        message_kind="system_prompt",
        visible_to_user=False,
    )
    add_message(
        session,
        conversation_id=conversation_id,
        content=response_text,
        message_kind="bot_reply",
        visible_to_user=True,
        client_payload=bot_client_payload,
    )
    session.commit()


def create_conversation_shell(
    session: Session,
    conversation_id: str,
    file_id: str,
    original_filename: str,
    fingerprint: str,
    attachment,
) -> None:
    """Create conversation and file records before long-running enrichment finishes."""
    session.add(
        Conversation(
            id=conversation_id,
            title=original_filename,
            original_filename=original_filename,
        )
    )
    session.add(
        FileRecord(
            id=file_id,
            conversation_id=conversation_id,
            filename=original_filename,
            fingerprint=fingerprint,
            poe_url=attachment.url,
            content_type=attachment.content_type,
            poe_name=attachment.name,
        )
    )
    session.commit()


def update_conversation_title(
    session: Session,
    conversation_id: str,
    title: str,
) -> Conversation | None:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        return None
    conversation.title = title
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation

def create_messages(
    session: Session,
    conversation_id: str,
    user_content: str,
    bot_content: str,
    *,
    user_message_kind: str = "user_message",
    user_section_category: str | None = None,
    user_visible_to_user: bool = True,
    bot_section_category: str | None = None,
    bot_client_payload: Any = None,
) -> None:
    """Create a pair of user and bot messages."""
    add_message(
        session,
        conversation_id=conversation_id,
        content=user_content,
        message_kind=user_message_kind,
        section_category=user_section_category,
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


def replace_figures(
    session: Session,
    conversation_id: str,
    figures: list[dict]
) -> None:
    """Replace extracted figures for a conversation."""
    existing_figures = get_figures(session, conversation_id)
    for figure in existing_figures:
        session.delete(figure)

    for figure in figures:
        session.add(PaperFigure(
            conversation_id=conversation_id,
            page_number=figure["page_number"],
            figure_index=figure["figure_index"],
            figure_label=figure.get("figure_label"),
            caption=figure["caption"],
            image_mime_type=figure.get("image_mime_type"),
            image_data=figure.get("image_data"),
            image_width=figure["image_width"],
            image_height=figure["image_height"],
        ))

    session.commit()


def replace_tables(
    session: Session,
    conversation_id: str,
    tables: list[dict]
) -> None:
    """Replace extracted tables for a conversation."""
    existing_tables = get_tables(session, conversation_id)
    for table in existing_tables:
        session.delete(table)

    for table in tables:
        session.add(PaperTable(
            conversation_id=conversation_id,
            page_number=table["page_number"],
            table_index=table["table_index"],
            table_label=table.get("table_label"),
            caption=table["caption"],
            image_mime_type=table.get("image_mime_type"),
            image_data=table.get("image_data"),
            image_width=table["image_width"],
            image_height=table["image_height"],
        ))

    session.commit()


def replace_tags(
    session: Session,
    conversation_id: str,
    tags: list[dict]
) -> None:
    """Replace extracted tags for a conversation."""
    existing_tags = get_tags(session, conversation_id)
    for tag in existing_tags:
        session.delete(tag)

    for tag in tags:
        session.add(PaperTag(
            conversation_id=conversation_id,
            category_code=tag["category_code"],
            category_label=tag["category_label"],
            tag_code=tag["tag_code"],
            tag_label=tag["tag_label"],
            tag_path=tag["tag_path"],
            source=tag.get("source", "poe"),
        ))

    session.commit()

def get_paged_conversations(session: Session, offset: int, limit: int) -> list[Conversation]:
    """Get a paginated list of conversations."""
    statement = (
        select(Conversation)
        .order_by(desc(Conversation.created_at))
        .offset(offset)
        .limit(limit)
    )
    return session.exec(statement).all()

def get_total_conversations_count(session: Session) -> int:
    """Get the total count of conversations."""
    return session.exec(select(func.count(Conversation.id))).one()


def create_async_job(
    session: Session,
    job_id: str,
    job_type: str,
    payload: dict,
    conversation_id: str | None = None,
) -> AsyncJob:
    now = datetime.now(timezone.utc)
    job = AsyncJob(
        id=job_id,
        job_type=job_type,
        status="queued",
        progress="排队中",
        payload_json=_json_dumps(payload),
        conversation_id=conversation_id,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def get_async_job(session: Session, job_id: str) -> AsyncJob | None:
    return session.get(AsyncJob, job_id)


def list_recoverable_async_jobs(session: Session) -> list[AsyncJob]:
    statement = (
        select(AsyncJob)
        .where(AsyncJob.status.in_(["queued", "running"]))
        .order_by(AsyncJob.created_at.asc())
    )
    return session.exec(statement).all()


def touch_async_job(
    session: Session,
    job_id: str,
    *,
    status: str | None = None,
    progress: str | None = None,
    result: dict | None = None,
    error_message: str | None = None,
    conversation_id: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> AsyncJob | None:
    job = session.get(AsyncJob, job_id)
    if not job:
        return None

    now = datetime.now(timezone.utc)
    if status is not None:
        job.status = status
    if progress is not None:
        job.progress = progress
    if result is not None:
        job.result_json = _json_dumps(result)
    if error_message is not None:
        job.error_message = error_message
    if conversation_id is not None:
        job.conversation_id = conversation_id
    if started and job.started_at is None:
        job.started_at = now
    if finished:
        job.finished_at = now
    job.updated_at = now
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def get_active_translation_job(session: Session, conversation_id: str) -> AsyncJob | None:
    statement = (
        select(AsyncJob)
        .where(
            AsyncJob.conversation_id == conversation_id,
            AsyncJob.job_type.in_(["translate_action"]),
            AsyncJob.status.in_(["queued", "running"]),
        )
        .order_by(AsyncJob.created_at.asc())
    )
    return session.exec(statement).first()


def _normalize_int(value: Any, default: int = 0, min_value: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if min_value is not None:
        number = max(min_value, number)
    return number


def _decode_asset_binary(payload: dict[str, Any]) -> bytes | None:
    raw_data = payload.get("image_data")
    if isinstance(raw_data, (bytes, bytearray)):
        return bytes(raw_data)
    if isinstance(raw_data, str) and raw_data.strip():
        try:
            return base64.b64decode(raw_data.strip(), validate=True)
        except Exception as exc:
            raise ValueError(f"Invalid image_data base64: {exc}") from exc

    b64_data = payload.get("image_data_base64")
    if isinstance(b64_data, str) and b64_data.strip():
        try:
            return base64.b64decode(b64_data.strip(), validate=True)
        except Exception as exc:
            raise ValueError(f"Invalid image_data_base64: {exc}") from exc
    return None


def persist_pipeline_bundle(session: Session, bundle: dict[str, Any]) -> dict[str, Any]:
    file_record_payload = bundle.get("file_record") or {}
    filename = str(file_record_payload.get("filename", "")).strip()
    fingerprint = str(file_record_payload.get("fingerprint", "")).strip()
    poe_url = str(file_record_payload.get("poe_url", "")).strip()

    if not filename:
        raise ValueError("file_record.filename is required.")
    if not fingerprint:
        raise ValueError("file_record.fingerprint is required.")
    if not poe_url:
        raise ValueError("file_record.poe_url is required.")

    existing_file = find_existing_file(session, fingerprint)
    if existing_file:
        return {
            "status": "succeeded",
            "exists": True,
            "conversation_id": existing_file.conversation_id,
            "committed_parts": [],
            "errors": [],
        }

    conversation_id = str(bundle.get("conversation_id", "")).strip() or uuid.uuid4().hex[:12]
    title = str(bundle.get("title", "")).strip() or filename
    content_type = str(file_record_payload.get("content_type", "")).strip() or "application/pdf"
    poe_name = str(file_record_payload.get("poe_name", "")).strip() or filename
    errors_payload = bundle.get("errors") if isinstance(bundle.get("errors"), list) else []
    committed_parts: list[str] = []
    now = datetime.now(timezone.utc)

    if session.get(Conversation, conversation_id):
        raise ValueError(f"conversation_id already exists: {conversation_id}")

    try:
        session.add(
            Conversation(
                id=conversation_id,
                title=title,
                original_filename=filename,
                created_at=now,
            )
        )
        committed_parts.append("conversation")

        session.add(
            FileRecord(
                id=uuid.uuid4().hex,
                conversation_id=conversation_id,
                filename=filename,
                fingerprint=fingerprint,
                poe_url=poe_url,
                content_type=content_type,
                poe_name=poe_name,
                uploaded_at=now,
            )
        )
        committed_parts.append("file_record")

        messages_payload = bundle.get("messages") if isinstance(bundle.get("messages"), list) else []
        for message in messages_payload:
            if not isinstance(message, dict):
                continue
            content = str(message.get("content", ""))
            message_kind = infer_message_kind(
                message_kind=str(message.get("message_kind", "")).strip() or None,
                message_type=str(message.get("message_type", "")).strip() or None,
                role=str(message.get("role", "")).strip() or None,
                content=content,
            )
            visible_to_user = message.get("visible_to_user")
            if visible_to_user is None:
                visible_to_user = is_bot_message_kind(message_kind) or message_kind == "user_message"
            add_message(
                session,
                conversation_id=conversation_id,
                content=content,
                message_kind=message_kind,
                section_category=message.get("section_category"),
                visible_to_user=bool(visible_to_user),
                client_payload=message.get("client_payload_json", message.get("client_payload")),
                created_at=now,
            )
        if messages_payload:
            committed_parts.append("messages")

        figures_payload = bundle.get("figures") if isinstance(bundle.get("figures"), list) else []
        for index, figure in enumerate(figures_payload):
            if not isinstance(figure, dict):
                continue
            session.add(
                PaperFigure(
                    conversation_id=conversation_id,
                    page_number=_normalize_int(figure.get("page_number"), default=1, min_value=1),
                    figure_index=_normalize_int(figure.get("figure_index"), default=index + 1, min_value=1),
                    figure_label=(str(figure.get("figure_label", "")).strip() or None),
                    caption=str(figure.get("caption", "")).strip(),
                    image_mime_type=str(figure.get("image_mime_type", "")).strip() or "image/webp",
                    image_data=_decode_asset_binary(figure),
                    image_width=_normalize_int(figure.get("image_width"), default=1, min_value=1),
                    image_height=_normalize_int(figure.get("image_height"), default=1, min_value=1),
                    created_at=now,
                )
            )
        if figures_payload:
            committed_parts.append("figures")

        tables_payload = bundle.get("tables") if isinstance(bundle.get("tables"), list) else []
        for index, table in enumerate(tables_payload):
            if not isinstance(table, dict):
                continue
            session.add(
                PaperTable(
                    conversation_id=conversation_id,
                    page_number=_normalize_int(table.get("page_number"), default=1, min_value=1),
                    table_index=_normalize_int(table.get("table_index"), default=index + 1, min_value=1),
                    table_label=(str(table.get("table_label", "")).strip() or None),
                    caption=str(table.get("caption", "")).strip(),
                    image_mime_type=str(table.get("image_mime_type", "")).strip() or "image/webp",
                    image_data=_decode_asset_binary(table),
                    image_width=_normalize_int(table.get("image_width"), default=1, min_value=1),
                    image_height=_normalize_int(table.get("image_height"), default=1, min_value=1),
                    created_at=now,
                )
            )
        if tables_payload:
            committed_parts.append("tables")

        tags_payload = bundle.get("tags") if isinstance(bundle.get("tags"), list) else []
        for tag in tags_payload:
            if not isinstance(tag, dict):
                continue
            tag_code = str(tag.get("tag_code", "")).strip()
            if not tag_code:
                continue
            category_code = str(tag.get("category_code", "")).strip()
            category_label = str(tag.get("category_label", "")).strip() or category_code
            tag_label = str(tag.get("tag_label", "")).strip() or tag_code
            tag_path = str(tag.get("tag_path", "")).strip() or f"{category_label}/{tag_label}"
            session.add(
                PaperTag(
                    conversation_id=conversation_id,
                    category_code=category_code,
                    category_label=category_label,
                    tag_code=tag_code,
                    tag_label=tag_label,
                    tag_path=tag_path,
                    source=str(tag.get("source", "")).strip() or "agent",
                    created_at=now,
                )
            )
        if tags_payload:
            committed_parts.append("tags")

        meta_payload = bundle.get("meta") if isinstance(bundle.get("meta"), dict) else None
        if meta_payload:
            raw_response_json = meta_payload.get("raw_response_json")
            if not isinstance(raw_response_json, str) or not raw_response_json.strip():
                raw_response_json = _json_dumps(meta_payload.get("raw_response")) if meta_payload.get("raw_response") is not None else "{}"

            status = str(meta_payload.get("status", "")).strip()
            if not status:
                status = "matched" if (
                    meta_payload.get("paper_id")
                    or meta_payload.get("venue")
                    or meta_payload.get("year")
                ) else "not_found"

            session.add(
                PaperSemanticScholarResult(
                    conversation_id=conversation_id,
                    status=status,
                    paper_id=str(meta_payload.get("paper_id", "")).strip() or None,
                    corpus_id=meta_payload.get("corpus_id"),
                    matched_title=str(meta_payload.get("matched_title", "")).strip() or None,
                    url=str(meta_payload.get("url", "")).strip() or None,
                    abstract=str(meta_payload.get("abstract", "")).strip() or None,
                    year=meta_payload.get("year"),
                    venue=str(meta_payload.get("venue", "")).strip() or None,
                    venue_abbr=str(meta_payload.get("venue_abbr", "")).strip(),
                    ccf_category=str(meta_payload.get("ccf_category", "")).strip() or "None",
                    ccf_type=str(meta_payload.get("ccf_type", "")).strip() or "None",
                    publication_date=str(meta_payload.get("publication_date", "")).strip() or None,
                    is_open_access=meta_payload.get("is_open_access"),
                    match_score=meta_payload.get("match_score"),
                    citation_count=meta_payload.get("citation_count"),
                    reference_count=meta_payload.get("reference_count"),
                    authors_json=meta_payload.get("authors_json"),
                    external_ids_json=meta_payload.get("external_ids_json"),
                    publication_types_json=meta_payload.get("publication_types_json"),
                    publication_venue_json=meta_payload.get("publication_venue_json"),
                    journal_json=meta_payload.get("journal_json"),
                    open_access_pdf_json=meta_payload.get("open_access_pdf_json"),
                    raw_response_json=raw_response_json,
                    source=str(meta_payload.get("source", "")).strip() or "agent",
                    created_at=now,
                    updated_at=now,
                )
            )
            committed_parts.append("meta")

        session.commit()
    except Exception:
        session.rollback()
        raise

    return {
        "status": "succeeded",
        "exists": False,
        "conversation_id": conversation_id,
        "committed_parts": committed_parts,
        "errors": errors_payload,
    }
