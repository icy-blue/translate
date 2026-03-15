from datetime import datetime, timezone
from typing import Optional

from sqlmodel import SQLModel, Field

class Conversation(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    original_filename: Optional[str] = None
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    role: str
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class FileRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    filename: str
    fingerprint: Optional[str] = Field(default=None, index=True)
    poe_url: str
    content_type: str
    poe_name: str
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
