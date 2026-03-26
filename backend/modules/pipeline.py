from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..app.dependencies import check_read_only, get_agent_ingest_token, get_db_session
from ..platform.models import Conversation, FileRecord, PaperFigure, PaperSemanticScholarResult, PaperTable, PaperTag
from .conversations import add_message, json_dumps
from .ingest import find_existing_file

router = APIRouter(tags=["pipeline"])


class PipelineMessagePayload(BaseModel):
    role: Optional[str] = None
    content: str
    message_kind: Optional[str] = None
    message_type: Optional[str] = None
    section_category: Optional[str] = None
    visible_to_user: Optional[bool] = None
    client_payload_json: Optional[str] = None
    client_payload: Optional[dict[str, Any]] = None


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
    raw_response: Optional[dict[str, Any]] = None
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
    messages: list[PipelineMessagePayload] = Field(default_factory=list)
    figures: list[PipelineAssetPayload] = Field(default_factory=list)
    tables: list[PipelineAssetPayload] = Field(default_factory=list)
    tags: list[PipelineTagPayload] = Field(default_factory=list)
    meta: Optional[PipelineMetaPayload] = None
    errors: list[PipelineErrorPayload] = Field(default_factory=list)


def _normalize_int(value: Any, default: int = 0, min_value: Optional[int] = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if min_value is not None:
        number = max(min_value, number)
    return number


def _decode_asset_binary(payload: dict[str, Any]) -> Optional[bytes]:
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
    from ..domain.message_kinds import infer_message_kind, is_bot_message_kind

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
        return {"status": "succeeded", "exists": True, "conversation_id": existing_file.conversation_id, "committed_parts": [], "errors": []}

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
        session.add(Conversation(id=conversation_id, title=title, original_filename=filename, created_at=now))
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
                    page_number=_normalize_int(figure.get("page_number"), 1, 1),
                    figure_index=_normalize_int(figure.get("figure_index"), index + 1, 1),
                    figure_label=(str(figure.get("figure_label", "")).strip() or None),
                    caption=str(figure.get("caption", "")).strip(),
                    image_mime_type=str(figure.get("image_mime_type", "")).strip() or "image/webp",
                    image_data=_decode_asset_binary(figure),
                    image_width=_normalize_int(figure.get("image_width"), 1, 1),
                    image_height=_normalize_int(figure.get("image_height"), 1, 1),
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
                    page_number=_normalize_int(table.get("page_number"), 1, 1),
                    table_index=_normalize_int(table.get("table_index"), index + 1, 1),
                    table_label=(str(table.get("table_label", "")).strip() or None),
                    caption=str(table.get("caption", "")).strip(),
                    image_mime_type=str(table.get("image_mime_type", "")).strip() or "image/webp",
                    image_data=_decode_asset_binary(table),
                    image_width=_normalize_int(table.get("image_width"), 1, 1),
                    image_height=_normalize_int(table.get("image_height"), 1, 1),
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
                raw_response_json = json_dumps(meta_payload.get("raw_response")) if meta_payload.get("raw_response") is not None else "{}"
            status = str(meta_payload.get("status", "")).strip()
            if not status:
                status = "matched" if (meta_payload.get("paper_id") or meta_payload.get("venue") or meta_payload.get("year")) else "not_found"
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

    return {"status": "succeeded", "exists": False, "conversation_id": conversation_id, "committed_parts": committed_parts, "errors": errors_payload}


@router.post("/pipeline/commits")
async def commit_pipeline_bundle_route(
    payload: PipelineBundlePayload,
    _agent_token: str = Depends(get_agent_ingest_token),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    try:
        return persist_pipeline_bundle(session, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist pipeline bundle: {exc}")
