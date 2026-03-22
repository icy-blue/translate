from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, LargeBinary
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


class PaperFigure(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    page_number: int
    figure_index: int
    figure_label: Optional[str] = None
    caption: str
    image_mime_type: Optional[str] = None
    image_data: Optional[bytes] = Field(default=None, sa_column=Column(LargeBinary))
    image_width: int
    image_height: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaperTable(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    page_number: int
    table_index: int
    table_label: Optional[str] = None
    caption: str
    image_mime_type: Optional[str] = None
    image_data: Optional[bytes] = Field(default=None, sa_column=Column(LargeBinary))
    image_width: int
    image_height: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaperTag(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    category_code: str = Field(index=True)
    category_label: str
    tag_code: str = Field(index=True)
    tag_label: str
    tag_path: str
    source: str = "poe"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
