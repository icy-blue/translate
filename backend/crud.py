from __future__ import annotations

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
)

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
