from __future__ import annotations

import json
from datetime import date, datetime, timezone

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


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)

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
    response_text: str
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
    session.add(Message(
        conversation_id=conversation_id,
        role="user",
        content=initial_prompt
    ))
    session.add(Message(
        conversation_id=conversation_id,
        role="bot",
        content=response_text
    ))
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
    bot_content: str
) -> None:
    """Create a pair of user and bot messages."""
    session.add(Message(
        conversation_id=conversation_id,
        role="user",
        content=user_content
    ))
    session.add(Message(
        conversation_id=conversation_id,
        role="bot",
        content=bot_content
    ))
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
            AsyncJob.job_type.in_(["continue", "custom_message"]),
            AsyncJob.status.in_(["queued", "running"]),
        )
        .order_by(AsyncJob.created_at.asc())
    )
    return session.exec(statement).first()
