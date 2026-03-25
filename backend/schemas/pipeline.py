from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PipelineMessagePayload(BaseModel):
    role: Optional[str] = None
    content: str
    message_kind: Optional[str] = None
    message_type: Optional[str] = None
    section_category: Optional[str] = None
    visible_to_user: Optional[bool] = None
    client_payload_json: Optional[str] = None
    client_payload: Optional[Dict[str, Any]] = None


class PipelineAssetPayload(BaseModel):
    page_number: int = 1
    caption: str = ""
    image_mime_type: str = "image/webp"
    image_data_base64: Optional[str] = None
    image_data: Optional[str] = None
    image_width: int = 1
    image_height: int = 1
    figure_index: Optional[int] = None
    figure_label: Optional[str] = None
    table_index: Optional[int] = None
    table_label: Optional[str] = None


class PipelineTagPayload(BaseModel):
    category_code: str = ""
    category_label: str = ""
    tag_code: str
    tag_label: str = ""
    tag_path: str = ""
    source: str = "agent"


class PipelineMetaPayload(BaseModel):
    status: Optional[str] = None
    paper_id: Optional[str] = None
    corpus_id: Optional[int] = None
    matched_title: Optional[str] = None
    url: Optional[str] = None
    abstract: Optional[str] = None
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
    authors_json: Optional[str] = None
    external_ids_json: Optional[str] = None
    publication_types_json: Optional[str] = None
    publication_venue_json: Optional[str] = None
    journal_json: Optional[str] = None
    open_access_pdf_json: Optional[str] = None
    raw_response_json: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    source: str = "agent"


class PipelineErrorPayload(BaseModel):
    skill: str = ""
    type: str = ""
    message: str = ""
    retryable: bool = False


class PipelineFileRecordPayload(BaseModel):
    filename: str
    fingerprint: str
    poe_url: str
    content_type: str = "application/pdf"
    poe_name: str = "upload.pdf"


class PipelineBundlePayload(BaseModel):
    conversation_id: Optional[str] = None
    title: str
    file_record: PipelineFileRecordPayload
    messages: List[PipelineMessagePayload] = Field(default_factory=list)
    figures: List[PipelineAssetPayload] = Field(default_factory=list)
    tables: List[PipelineAssetPayload] = Field(default_factory=list)
    tags: List[PipelineTagPayload] = Field(default_factory=list)
    meta: Optional[PipelineMetaPayload] = None
    errors: List[PipelineErrorPayload] = Field(default_factory=list)
