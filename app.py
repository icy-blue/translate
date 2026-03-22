from __future__ import annotations

import hashlib
import io
import re
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import fastapi_poe as fp
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import inspect, text
from pypdf import PdfReader, PdfWriter
from sqlmodel import Session, select, func

import crud
from config import settings
from database import engine
from dependencies import get_db_session, check_read_only, get_api_key
from models import SQLModel, Conversation, Message, PaperFigure, PaperTable, PaperTag
from paper_tags import build_tag_payloads, extract_abstract_for_tagging, get_tag_definition, get_tag_library_payload
from pdf_figures import extract_pdf_figures, extract_pdf_tables
from poe_utils import classify_paper_tags, extract_title_from_pdf, get_bot_response, upload_file

app = FastAPI()
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    SQLModel.metadata.create_all(engine)
    _ensure_asset_columns()

# Endpoint to handle PDF uploads and start the translation process
@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    poe_model: str = Form(default="GPT-5.2-Instant"),
    title_model: str = Form(default="GPT-5.2-Instant"),
    tag_model: str = Form(default="GPT-5.2-Instant"),
    extract_tags: bool = Form(default=False),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only)
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    fingerprint = hashlib.sha256(file_bytes).hexdigest()

    # Check if a file with the same content has already been processed
    existing_file = crud.find_existing_file(session, fingerprint)
    if existing_file:
        conversation = crud.get_conversation(session, existing_file.conversation_id)
        messages = crud.get_messages(session, existing_file.conversation_id)
        figures = _extract_and_store_figures(session, existing_file.conversation_id, file_bytes)
        tables = _extract_and_store_tables(session, existing_file.conversation_id, file_bytes)
        tags = crud.get_tags(session, existing_file.conversation_id)
        if extract_tags and not tags and conversation:
            first_bot_message = next((message.content for message in messages if message.role == "bot"), "")
            tags = await _extract_and_store_tags(
                session,
                existing_file.conversation_id,
                conversation.title or existing_file.filename,
                first_bot_message,
                tag_model,
                api_key,
            )
        
        def keep(m):
            return m.role != "user" or (m.content != settings.initial_prompt and m.content != "继续")
        
        return {
            "conversation_id": existing_file.conversation_id,
            "title": conversation.title if conversation else None,
            "messages": [{"role": m.role, "content": m.content} for m in messages if keep(m)],
            "exists": True,
            "pdf_url": existing_file.poe_url,
            "figures": _serialize_figures(figures),
            "tables": _serialize_tables(tables),
            "tags": _serialize_tags(tags),
        }

    # Generate unique IDs for the conversation and file
    conversation_id = uuid.uuid4().hex[:12]
    file_id = uuid.uuid4().hex

    # Upload the PDF to Poe's CDN
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        with open(tmp.name, "rb") as f:
            pdf_attachment = await upload_file(f, api_key, file.filename)

    # For title extraction, create a new PDF with only the first page
    title_extraction_attachment = None
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        if len(reader.pages) > 0:
            writer = PdfWriter()
            writer.add_page(reader.pages[0])
            first_page_pdf_bytes = io.BytesIO()
            writer.write(first_page_pdf_bytes)
            first_page_pdf_bytes.seek(0)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_first_page:
                tmp_first_page.write(first_page_pdf_bytes.getvalue())
                tmp_first_page.flush()
                with open(tmp_first_page.name, "rb") as f:
                    title_extraction_attachment = await upload_file(f, api_key, f"first_page_{file.filename}")
    except Exception as e:
        print(f"Error processing PDF for title extraction: {e}")
        title_extraction_attachment = pdf_attachment

    # Extract the title and get the initial translation
    extracted_title = await extract_title_from_pdf(title_extraction_attachment or pdf_attachment, api_key, title_model)
    initial_prompt = settings.initial_prompt
    message = fp.ProtocolMessage(role="user", content=initial_prompt, attachments=[pdf_attachment])
    response_text = await get_bot_response([message], poe_model, api_key)

    # Save the new conversation and associated data to the database
    final_title = extracted_title or file.filename
    crud.create_conversation_package(
        session,
        conversation_id,
        file_id,
        final_title,
        file.filename,
        fingerprint,
        pdf_attachment,
        initial_prompt,
        response_text
    )
    figures = _extract_and_store_figures(session, conversation_id, file_bytes)
    tables = _extract_and_store_tables(session, conversation_id, file_bytes)
    tags = await _extract_and_store_tags(session, conversation_id, final_title, response_text, tag_model, api_key) if extract_tags else []

    return {
        "conversation_id": conversation_id,
        "title": final_title,
        "messages": [{"role": "bot", "content": response_text}],
        "pdf_url": pdf_attachment.url,
        "figures": _serialize_figures(figures),
        "tables": _serialize_tables(tables),
        "tags": _serialize_tags(tags),
    }

# Common logic for continuing a conversation
async def _continue_conversation(conversation_id: str, new_user_message: str, poe_model: str, api_key: str, session: Session, save_to_record: bool):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    file_record = crud.get_file_record(session, conversation_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    db_messages = crud.get_messages(session, conversation_id)
    pdf_attachment = fp.Attachment(url=file_record.poe_url, content_type=file_record.content_type, name=file_record.poe_name)

    poe_messages = [
        fp.ProtocolMessage(role="user", content=m.content, attachments=[pdf_attachment]) if i == 0 and m.role == "user" else fp.ProtocolMessage(role=m.role, content=m.content)
        for i, m in enumerate(db_messages)
    ]
    poe_messages.append(fp.ProtocolMessage(role="user", content=new_user_message))

    response_text = await get_bot_response(poe_messages, poe_model, api_key)

    if save_to_record:
        crud.create_messages(session, conversation_id, new_user_message, response_text)

    return {"reply": response_text}

# Endpoint to continue an existing translation
@app.post("/continue/{conversation_id}")
async def continue_translation(
    conversation_id: str,
    poe_model: str = Form(default="GPT-5.2-Instant"),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only)
):
    return await _continue_conversation(conversation_id, "继续", poe_model, api_key, session, True)

# Endpoint for sending a custom message in a conversation
@app.post("/custom_message/{conversation_id}")
async def custom_message(
    conversation_id: str,
    message: str = Form(...),
    save_to_record: bool = Form(...),
    poe_model: str = Form(default="GPT-5.2-Instant"),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only)
):
    if not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    return await _continue_conversation(conversation_id, message, poe_model, api_key, session, save_to_record)

# Endpoint to retrieve a full conversation
@app.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str, session: Session = Depends(get_db_session)):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    messages = crud.get_messages(session, conversation_id)
    file_record = crud.get_file_record(session, conversation_id)
    figures = crud.get_figures(session, conversation_id)
    tables = crud.get_tables(session, conversation_id)
    tags = crud.get_tags(session, conversation_id)
    pdf_url = file_record.poe_url if file_record else None

    def keep(m):
        return m.role != "user" or (m.content != settings.initial_prompt and m.content != "继续")

    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": _ensure_utc_timezone(conversation.created_at),
        "messages": [{"role": m.role, "content": m.content} for m in messages if keep(m)],
        "pdf_url": pdf_url,
        "figures": _serialize_figures(figures),
        "tables": _serialize_tables(tables),
        "tags": _serialize_tags(tags),
    }


@app.get("/assets/figures/{figure_id}")
async def get_figure_asset(figure_id: int, session: Session = Depends(get_db_session)):
    figure = session.get(PaperFigure, figure_id)
    return _build_asset_response(figure)

@app.get("/assets/tables/{table_id}")
async def get_table_asset(table_id: int, session: Session = Depends(get_db_session)):
    table = session.get(PaperTable, table_id)
    return _build_asset_response(table)


@app.post("/conversation/{conversation_id}/reprocess_assets")
async def reprocess_assets(
    conversation_id: str,
    asset_type: Optional[str] = Form(default=None),
    caption_direction: Optional[str] = Form(default=None),
    figure_caption_direction: Optional[str] = Form(default=None),
    table_caption_direction: Optional[str] = Form(default=None),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    if asset_type is not None or caption_direction is not None:
        if asset_type not in {"figure", "table"}:
            raise HTTPException(status_code=400, detail="asset_type must be 'figure' or 'table'.")
        if caption_direction not in {"above", "below"}:
            raise HTTPException(status_code=400, detail="caption_direction must be 'above' or 'below'.")
        if asset_type == "figure":
            figure_caption_direction = caption_direction
        else:
            table_caption_direction = caption_direction

    for field_name, value in {
        "figure_caption_direction": figure_caption_direction,
        "table_caption_direction": table_caption_direction,
    }.items():
        if value is not None and value not in {"above", "below"}:
            raise HTTPException(status_code=400, detail=f"{field_name} must be 'above' or 'below'.")
    if figure_caption_direction is None and table_caption_direction is None:
        raise HTTPException(status_code=400, detail="At least one caption direction must be provided.")

    file_record = crud.get_file_record(session, conversation_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    try:
        file_bytes = _download_pdf_bytes(file_record.poe_url)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    figures = crud.get_figures(session, conversation_id)
    tables = crud.get_tables(session, conversation_id)

    if figure_caption_direction is not None:
        figures = _extract_and_store_figures(session, conversation_id, file_bytes, figure_caption_direction)
    if table_caption_direction is not None:
        tables = _extract_and_store_tables(session, conversation_id, file_bytes, table_caption_direction)

    return {
        "figure_caption_direction": figure_caption_direction,
        "table_caption_direction": table_caption_direction,
        "figures": _serialize_figures(figures),
        "tables": _serialize_tables(tables),
    }

# Helper to ensure datetime objects have UTC timezone information
def _ensure_utc_timezone(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

# Helper to build a conversation data object for API responses
def _build_conversation_data(session: Session, conversation: Conversation, include_relevance: bool = False, relevance_score: int = 0) -> dict:
    msg_statement = select(Message).where(Message.conversation_id == conversation.id, Message.role == "bot").order_by(Message.id)
    first_bot_msg = session.exec(msg_statement).first()
    summary = (first_bot_msg.content[:200] + "...") if first_bot_msg and len(first_bot_msg.content) > 200 else (first_bot_msg.content if first_bot_msg else "")

    file_record = crud.get_file_record(session, conversation.id)
    pdf_url = file_record.poe_url if file_record else None
    tags = crud.get_tags(session, conversation.id)

    result = {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": _ensure_utc_timezone(conversation.created_at),
        "summary": summary,
        "pdf_url": pdf_url,
        "tags": _serialize_tags(tags),
    }
    if include_relevance:
        result["relevance"] = relevance_score
    return result


def _serialize_figures(figures) -> list[dict]:
    return [
        {
            "id": figure.id,
            "page_number": figure.page_number,
            "figure_index": figure.figure_index,
            "figure_label": figure.figure_label,
            "caption": figure.caption,
            "image_url": f"/assets/figures/{figure.id}",
            "image_width": figure.image_width,
            "image_height": figure.image_height,
        }
        for figure in figures
    ]


def _serialize_tables(tables) -> list[dict]:
    return [
        {
            "id": table.id,
            "page_number": table.page_number,
            "table_index": table.table_index,
            "table_label": table.table_label,
            "caption": table.caption,
            "image_url": f"/assets/tables/{table.id}",
            "image_width": table.image_width,
            "image_height": table.image_height,
        }
        for table in tables
    ]


def _serialize_tags(tags) -> list[dict]:
    serialized: list[dict] = []
    for tag in tags:
        tag_definition = get_tag_definition(tag.tag_code)
        serialized.append(
            {
                "id": tag.id,
                "category_code": tag.category_code,
                "category_label": tag_definition.category_label if tag_definition else tag.category_label,
                "category_label_en": tag_definition.category_label_en if tag_definition else "",
                "tag_code": tag.tag_code,
                "tag_label": tag_definition.tag_label if tag_definition else tag.tag_label,
                "tag_label_en": tag_definition.tag_label_en if tag_definition else "",
                "tag_path": tag_definition.path if tag_definition else tag.tag_path,
                "tag_path_en": tag_definition.path_en if tag_definition else "",
                "source": tag.source,
            }
        )
    return serialized


def _extract_and_store_figures(
    session: Session,
    conversation_id: str,
    file_bytes: bytes,
    preferred_direction: str | None = None,
):
    try:
        extracted_figures = extract_pdf_figures(file_bytes, preferred_direction=preferred_direction)
        crud.replace_figures(session, conversation_id, extracted_figures)
        return crud.get_figures(session, conversation_id)
    except Exception as e:
        print(f"Error extracting figures for conversation {conversation_id}: {e}")
        session.rollback()
        return crud.get_figures(session, conversation_id)


def _extract_and_store_tables(
    session: Session,
    conversation_id: str,
    file_bytes: bytes,
    preferred_direction: str | None = None,
):
    try:
        extracted_tables = extract_pdf_tables(file_bytes, preferred_direction=preferred_direction)
        crud.replace_tables(session, conversation_id, extracted_tables)
        return crud.get_tables(session, conversation_id)
    except Exception as e:
        print(f"Error extracting tables for conversation {conversation_id}: {e}")
        session.rollback()
        return crud.get_tables(session, conversation_id)


async def _extract_and_store_tags(
    session: Session,
    conversation_id: str,
    title: str,
    first_bot_message: str,
    tag_model: str,
    api_key: str,
):
    abstract = extract_abstract_for_tagging(first_bot_message)
    if not title or not abstract:
        return crud.get_tags(session, conversation_id)

    try:
        extracted_tags = await classify_paper_tags(title, abstract, tag_model, api_key)
        if extracted_tags:
            crud.replace_tags(session, conversation_id, extracted_tags)
        return crud.get_tags(session, conversation_id)
    except Exception as exc:
        print(f"Error extracting tags for conversation {conversation_id}: {exc}")
        session.rollback()
        return crud.get_tags(session, conversation_id)


def _ensure_asset_columns():
    dialect = engine.dialect.name
    binary_type = "BYTEA" if dialect == "postgresql" else "BLOB"

    required_columns = {
        "paperfigure": {
            "image_mime_type": "VARCHAR",
            "image_data": binary_type,
        },
        "papertable": {
            "image_mime_type": "VARCHAR",
            "image_data": binary_type,
        },
    }

    with engine.begin() as connection:
        inspector = inspect(connection)
        for table_name, columns in required_columns.items():
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
            if "image_url" in existing_columns:
                connection.execute(text(f"ALTER TABLE {table_name} DROP COLUMN image_url"))


def _build_asset_response(asset):
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    if asset.image_data is not None:
        return Response(content=bytes(asset.image_data), media_type=asset.image_mime_type or "image/webp")
    raise HTTPException(status_code=404, detail="Asset data not found.")


def _download_pdf_bytes(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "translate-reprocess/1.0",
            "Accept": "application/pdf,*/*",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"Failed to download PDF from {url}: {exc}") from exc

# Helper to build a list of conversation data objects
def _build_conversations_data(session: Session, conversations: list[Conversation], include_relevance: bool = False, relevance_scores: list[int] = None) -> list[dict]:
    relevance_scores = relevance_scores or ([0] * len(conversations))
    return [_build_conversation_data(session, conv, include_relevance, relevance_scores[i]) for i, conv in enumerate(conversations)]


def _normalize_tag_codes(tag_codes: Optional[List[str]]) -> list[str]:
    if not tag_codes:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for tag_code in tag_codes:
        if not tag_code:
            continue
        code = tag_code.strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return normalized


def _build_tagged_conversation_statement(tag_codes: Optional[List[str]]):
    statement = select(Conversation)
    normalized_tag_codes = _normalize_tag_codes(tag_codes)
    if normalized_tag_codes:
        tagged_conversation_ids = (
            select(PaperTag.conversation_id)
            .where(PaperTag.tag_code.in_(normalized_tag_codes))
            .group_by(PaperTag.conversation_id)
            .having(func.count(func.distinct(PaperTag.tag_code)) == len(normalized_tag_codes))
        )
        statement = statement.where(Conversation.id.in_(tagged_conversation_ids))
    return statement


def _get_tag_usage_counts(session: Session) -> dict[str, int]:
    statement = (
        select(PaperTag.tag_code, func.count(func.distinct(PaperTag.conversation_id)))
        .group_by(PaperTag.tag_code)
    )
    return {tag_code: count for tag_code, count in session.exec(statement).all()}

# Endpoint to list conversations with pagination
@app.get("/conversations")
async def list_conversations(
    limit: int = 10,
    offset: int = 0,
    tag_code: Optional[List[str]] = Query(default=None),
    session: Session = Depends(get_db_session),
):
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    normalized_tag_codes = _normalize_tag_codes(tag_code)

    total_statement = select(func.count(Conversation.id))
    conversations_statement = _build_tagged_conversation_statement(normalized_tag_codes).order_by(Conversation.created_at.desc())
    if normalized_tag_codes:
        total_statement = total_statement.where(
            Conversation.id.in_(
                select(PaperTag.conversation_id)
                .where(PaperTag.tag_code.in_(normalized_tag_codes))
                .group_by(PaperTag.conversation_id)
                .having(func.count(func.distinct(PaperTag.tag_code)) == len(normalized_tag_codes))
            )
        )
    total = session.exec(total_statement).one()
    conversations = session.exec(conversations_statement.offset(offset).limit(limit + 1)).all()
    
    has_more = len(conversations) > limit
    conversations = conversations[:limit]

    result = _build_conversations_data(session, conversations)
    return {"conversations": result, "has_more": has_more, "total": total}

# Search logic
def _calculate_relevance(title: str, query: str) -> int:
    if not title: return 0
    title_lower, query_lower = title.lower(), query.lower()
    if query_lower == title_lower: return 100
    if query_lower in title_lower: return 50
    return 0

@app.get("/search")
async def search_conversations(
    q: str = "",
    search_type: str = "all",
    tag_code: Optional[List[str]] = Query(default=None),
    session: Session = Depends(get_db_session),
):
    normalized_tag_codes = _normalize_tag_codes(tag_code)
    if not (q and q.strip()) and not normalized_tag_codes:
        return {"exact_matches": [], "fuzzy_matches": []}

    query = q.strip()
    base_statement = _build_tagged_conversation_statement(normalized_tag_codes)
    
    # Exact search
    if query:
        exact_statement = base_statement.where(Conversation.title.ilike(f"%{query}%")).order_by(Conversation.created_at.desc()).limit(5)
        exact_convs = session.exec(exact_statement).all()
        exact_relevance_scores = [_calculate_relevance(c.title or "", query) for c in exact_convs]
    else:
        exact_statement = base_statement.order_by(Conversation.created_at.desc()).limit(10)
        exact_convs = session.exec(exact_statement).all()
        exact_relevance_scores = [100] * len(exact_convs)
    exact_matches = _build_conversations_data(session, exact_convs, True, exact_relevance_scores)

    # Fuzzy search
    fuzzy_matches = []
    query_words = [w.lower() for w in query.split() if len(w) > 1]
    if query_words:
        all_fuzzy_statement = base_statement.where(~Conversation.title.ilike(f"%{query}%")).order_by(Conversation.created_at.desc())
        all_fuzzy = session.exec(all_fuzzy_statement).all()
        fuzzy_candidates = []
        for c in all_fuzzy:
            title = (c.title or "").lower()
            relevance = sum(len(word) + 5 if re.search(r'\b' + re.escape(word) + r'\b', title) else len(word) for word in query_words if word in title)
            if relevance > 0:
                fuzzy_candidates.append((c, relevance))
        
        fuzzy_candidates.sort(key=lambda x: (-x[1], x[0].created_at))
        fuzzy_convs = [c for c, _ in fuzzy_candidates[:5]]
        fuzzy_relevance_scores = [r for _, r in fuzzy_candidates[:5]]
        fuzzy_matches = _build_conversations_data(session, fuzzy_convs, True, fuzzy_relevance_scores)

    return {
        "exact_matches": exact_matches if search_type != "fuzzy" else [],
        "fuzzy_matches": fuzzy_matches if search_type != "exact" else []
    }


@app.get("/tags/library")
async def get_tag_library(session: Session = Depends(get_db_session)):
    return {"categories": get_tag_library_payload(_get_tag_usage_counts(session))}


@app.post("/conversation/{conversation_id}/tags")
async def update_conversation_tags(
    conversation_id: str,
    tag_code: Optional[List[str]] = Form(default=None),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    normalized_tag_codes = _normalize_tag_codes(tag_code)
    crud.replace_tags(session, conversation_id, build_tag_payloads(normalized_tag_codes, source="manual"))
    return {"tags": _serialize_tags(crud.get_tags(session, conversation_id))}

# Static file serving
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_root():
    return FileResponse("static/index.html")

@app.get("/chat/{path:path}")
async def serve_chat_paths(path: str):
    return FileResponse("static/index.html")

@app.get("/chat")
async def serve_chat_paths():
    return FileResponse("static/index.html")

# System configuration endpoint
@app.get("/config")
async def get_config():
    return {"read_only": settings.read_only}
