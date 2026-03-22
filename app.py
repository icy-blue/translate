import hashlib
import io
import re
import tempfile
import uuid
from datetime import datetime, timezone

import fastapi_poe as fp
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pypdf import PdfReader, PdfWriter
from sqlmodel import Session, select

import crud
from config import settings
from database import engine
from dependencies import get_db_session, check_read_only, get_api_key
from models import SQLModel, Conversation, Message
from poe_utils import extract_title_from_pdf, get_bot_response, upload_file

app = FastAPI()

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

# Endpoint to handle PDF uploads and start the translation process
@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    poe_model: str = Form(default="GPT-5.2-Instant"),
    title_model: str = Form(default="GPT-5.2-Instant"),
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
        
        def keep(m):
            return m.role != "user" or (m.content != settings.initial_prompt and m.content != "继续")
        
        return {
            "conversation_id": existing_file.conversation_id,
            "title": conversation.title if conversation else None,
            "messages": [{"role": m.role, "content": m.content} for m in messages if keep(m)],
            "exists": True,
            "pdf_url": existing_file.poe_url
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

    return {
        "conversation_id": conversation_id,
        "title": final_title,
        "messages": [{"role": "bot", "content": response_text}],
        "pdf_url": pdf_attachment.url
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
    pdf_url = file_record.poe_url if file_record else None

    def keep(m):
        return m.role != "user" or (m.content != settings.initial_prompt and m.content != "继续")

    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": _ensure_utc_timezone(conversation.created_at),
        "messages": [{"role": m.role, "content": m.content} for m in messages if keep(m)],
        "pdf_url": pdf_url
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

    result = {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": _ensure_utc_timezone(conversation.created_at),
        "summary": summary,
        "pdf_url": pdf_url
    }
    if include_relevance:
        result["relevance"] = relevance_score
    return result

# Helper to build a list of conversation data objects
def _build_conversations_data(session: Session, conversations: list[Conversation], include_relevance: bool = False, relevance_scores: list[int] = None) -> list[dict]:
    relevance_scores = relevance_scores or ([0] * len(conversations))
    return [_build_conversation_data(session, conv, include_relevance, relevance_scores[i]) for i, conv in enumerate(conversations)]

# Endpoint to list conversations with pagination
@app.get("/conversations")
async def list_conversations(limit: int = 10, offset: int = 0, session: Session = Depends(get_db_session)):
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    
    total = crud.get_total_conversations_count(session)
    conversations = crud.get_paged_conversations(session, offset, limit + 1)
    
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
async def search_conversations(q: str, search_type: str = "all", session: Session = Depends(get_db_session)):
    if not q or not q.strip():
        return {"exact_matches": [], "fuzzy_matches": []}

    query = q.strip()
    
    # Exact search
    exact_convs = session.exec(select(Conversation).where(Conversation.title.ilike(f"%{query}%")).order_by(Conversation.created_at.desc()).limit(5)).all()
    exact_relevance_scores = [_calculate_relevance(c.title or "", query) for c in exact_convs]
    exact_matches = _build_conversations_data(session, exact_convs, True, exact_relevance_scores)

    # Fuzzy search
    fuzzy_matches = []
    query_words = [w.lower() for w in query.split() if len(w) > 1]
    if query_words:
        all_fuzzy = session.exec(select(Conversation).where(~Conversation.title.ilike(f"%{query}%")).order_by(Conversation.created_at.desc())).all()
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
