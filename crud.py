from sqlmodel import Session, select, func
from sqlalchemy import desc

from models import Conversation, Message, FileRecord

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
