from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, DateTime, LargeBinary, Text
from sqlmodel import SQLModel, Field


class Conversation(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    original_filename: Optional[str] = None
    status: str = "active"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    role: str
    content: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class FileRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)
    filename: str
    fingerprint: Optional[str] = Field(default=None, index=True)
    poe_url: str
    content_type: str
    poe_name: str
    uploaded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


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
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


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
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class PaperTag(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    category_code: str = Field(index=True)
    category_label: str
    tag_code: str = Field(index=True)
    tag_label: str
    tag_path: str
    source: str = "poe"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class PaperSemanticScholarResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: str = Field(index=True)
    status: str = Field(index=True)
    paper_id: Optional[str] = Field(default=None, index=True)
    corpus_id: Optional[int] = Field(default=None, index=True)
    matched_title: Optional[str] = None
    url: Optional[str] = None
    abstract: Optional[str] = Field(default=None, sa_column=Column(Text))
    year: Optional[int] = None
    venue: Optional[str] = None
    venue_abbr: str = ""
    ccf_category: str = "None"
    ccf_type: str = "None"
    publication_date: Optional[str] = None
    is_open_access: Optional[bool] = None
    match_score: Optional[float] = None
    citation_count: Optional[int] = None
    reference_count: Optional[int] = None
    authors_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    external_ids_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    publication_types_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    publication_venue_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    journal_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    open_access_pdf_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    raw_response_json: str = Field(sa_column=Column(Text))
    source: str = "semantic_scholar"
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class AsyncJob(SQLModel, table=True):
    id: str = Field(primary_key=True)
    job_type: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    progress: Optional[str] = None
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    result_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    error_message: Optional[str] = Field(default=None, sa_column=Column(Text))
    conversation_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    finished_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
