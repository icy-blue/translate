import uuid
import tempfile
import hashlib
import re
from datetime import datetime
from typing import Optional

import fastapi_poe as fp
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Field, create_engine, Session, select


# Database configuration & other settings (loaded from config)
from config import settings

DATABASE_URL = settings.database_url
engine = create_engine(DATABASE_URL, echo=False)


# Data models

class Conversation(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: Optional[str] = None
    original_filename: Optional[str] = None
    status: str = "active"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Message(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)  # auto-increment ensures order
    conversation_id: str = Field(index=True)
    role: str
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class FileRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    conversation_id: str = Field(index=True)

    # local file metadata
    filename: str

    # fingerprint for deduplication
    fingerprint: Optional[str] = Field(default=None, index=True)

    # Poe CDN attachment info    
    poe_url: str
    content_type: str
    poe_name: str

    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


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
    # Temporary files are used for uploads; no persistent uploads/ directory required


# helper: get title via Poe prompt
async def extract_title_from_pdf(pdf_attachment: fp.Attachment, api_key: str) -> Optional[str]:
    prompt = settings.title_prompt
    message = fp.ProtocolMessage(role="user", content=prompt, attachments=[pdf_attachment])
    title_text = ""
    async for part in fp.get_bot_response(
        messages=[message],
        bot_name=settings.poe_model,
        api_key=api_key
    ):
        title_text += part.text
    return title_text.strip() or None

# Upload PDF and start translation

@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    api_key: str = Form(...)
):
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key is required")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files supported")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    # compute SHA256 fingerprint for deduplication
    fingerprint = hashlib.sha256(file_bytes).hexdigest()

    # check existing fingerprint and shortcut
    with Session(engine) as session:
        existing = session.exec(
            select(FileRecord).where(FileRecord.fingerprint == fingerprint)
        ).first()

        if existing:
            # fetch conversation title for existing record
            conv = session.get(Conversation, existing.conversation_id)
            # collect all messages for frontend display (preserve roles)
            stmt = (
                select(Message)
                .where(Message.conversation_id == existing.conversation_id)
                .order_by(Message.id)
            )
            msgs = session.exec(stmt).all()
            # filter user messages that are just the initial prompt or the continuation keyword
            def keep(m):
                if m.role != "user":
                    return True
                if m.content == settings.initial_prompt:
                    return False
                if m.content == "继续":
                    return False
                return True
            all_messages = [{"role": m.role, "content": m.content} for m in msgs if keep(m)]

            return {
                "conversation_id": existing.conversation_id,
                "title": conv.title if conv else None,
                "messages": all_messages,
                "exists": True
            }

    conversation_id = uuid.uuid4().hex[:12]
    file_id = uuid.uuid4().hex

    # write bytes to a temp file for libraries requiring a filepath
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        temp_path = tmp.name

        # upload to Poe CDN once using the temporary file
        with open(temp_path, "rb") as f:
            pdf_attachment = await fp.upload_file(f, api_key=api_key, file_name=file.filename)

    # title extraction via a separate ephemeral conversation
    extracted_title = await extract_title_from_pdf(pdf_attachment, api_key)

    initial_prompt = settings.initial_prompt

    message = fp.ProtocolMessage(
        role="user",
        content=initial_prompt,
        attachments=[pdf_attachment]
    )

    response_text = ""

    async for partial in fp.get_bot_response(
        messages=[message],
        bot_name=settings.poe_model,
        api_key=api_key
    ):
        response_text += partial.text

    # persist records to database
    with Session(engine) as session:

        # extract title using a separate Poe prompt
        extracted_title = await extract_title_from_pdf(pdf_attachment, api_key)
        final_title = extracted_title or file.filename

        session.add(Conversation(
            id=conversation_id,
            title=final_title,
            original_filename=file.filename
        ))

        session.add(FileRecord(
            id=file_id,
            conversation_id=conversation_id,
            filename=file.filename,
            fingerprint=fingerprint,
            poe_url=pdf_attachment.url,
            content_type=pdf_attachment.content_type,
            poe_name=pdf_attachment.name
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

    return {
        "conversation_id": conversation_id,
        "title": final_title,
        "messages": [
            {"role": "bot", "content": response_text}
        ]
    }

@app.post("/continue/{conversation_id}")
async def continue_translation(
    conversation_id: str,
    api_key: str = Form(...)
):
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key required")

    with Session(engine) as session:

        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        file_record = session.exec(
            select(FileRecord)
            .where(FileRecord.conversation_id == conversation_id)
        ).first()

        if not file_record:
            raise HTTPException(status_code=404, detail="File record not found")

        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)   # ✅ 顺序保证
        )

        db_messages = session.exec(statement).all()

    # rebuild Poe attachment without reuploading
    pdf_attachment = fp.Attachment(
        url=file_record.poe_url,
        content_type=file_record.content_type,
        name=file_record.poe_name
    )

    poe_messages = []

    for i, m in enumerate(db_messages):
        if i == 0 and m.role == "user":
            # attach the PDF file on the initial user message
            poe_messages.append(
                fp.ProtocolMessage(
                    role="user",
                    content=m.content,
                    attachments=[pdf_attachment]
                )
            )
        else:
            poe_messages.append(
                fp.ProtocolMessage(
                    role=m.role,
                    content=m.content
                )
            )

    poe_messages.append(
        fp.ProtocolMessage(role="user", content="继续")
    )

    response_text = ""

    async for partial in fp.get_bot_response(
        messages=poe_messages,
        bot_name=settings.poe_model,
        api_key=api_key
    ):
        response_text += partial.text

    # persist new conversation messages
    with Session(engine) as session:
        session.add(Message(
            conversation_id=conversation_id,
            role="user",
            content="继续"
        ))
        session.add(Message(
            conversation_id=conversation_id,
            role="bot",
            content=response_text
        ))
        session.commit()

    return {"reply": response_text}


@app.post("/custom_message/{conversation_id}")
async def custom_message(
    conversation_id: str,
    message: str = Form(...),
    save_to_record: bool = Form(...),
    api_key: str = Form(...)
):
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key required")

    if not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    with Session(engine) as session:

        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        file_record = session.exec(
            select(FileRecord)
            .where(FileRecord.conversation_id == conversation_id)
        ).first()

        if not file_record:
            raise HTTPException(status_code=404, detail="File record not found")

        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )

        db_messages = session.exec(statement).all()

    # rebuild Poe attachment without reuploading
    pdf_attachment = fp.Attachment(
        url=file_record.poe_url,
        content_type=file_record.content_type,
        name=file_record.poe_name
    )

    poe_messages = []

    for i, m in enumerate(db_messages):
        if i == 0 and m.role == "user":
            # attach the PDF file on the initial user message
            poe_messages.append(
                fp.ProtocolMessage(
                    role="user",
                    content=m.content,
                    attachments=[pdf_attachment]
                )
            )
        else:
            poe_messages.append(
                fp.ProtocolMessage(
                    role=m.role,
                    content=m.content
                )
            )

    poe_messages.append(
        fp.ProtocolMessage(role="user", content=message)
    )

    response_text = ""

    async for partial in fp.get_bot_response(
        messages=poe_messages,
        bot_name=settings.poe_model,
        api_key=api_key
    ):
        response_text += partial.text

    # persist new conversation messages if save_to_record is True
    if save_to_record:
        with Session(engine) as session:
            session.add(Message(
                conversation_id=conversation_id,
                role="user",
                content=message
            ))
            session.add(Message(
                conversation_id=conversation_id,
                role="bot",
                content=response_text
            ))
            session.commit()

    return {"reply": response_text}


# Retrieve full conversation

@app.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str):
    with Session(engine) as session:

        conversation = session.get(Conversation, conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        statement = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id)
        )

        messages = session.exec(statement).all()

        # filter out initial prompt and continuation marks on every retrieval
        def keep(m):
            if m.role != "user":
                return True
            if m.content == settings.initial_prompt:
                return False
            if m.content == "继续":
                return False
            return True

        return {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": conversation.created_at,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in messages if keep(m)
            ]
        }


# List conversations

@app.get("/conversations")
async def list_conversations():
    with Session(engine) as session:
        statement = select(Conversation).order_by(Conversation.created_at.desc())
        conversations = session.exec(statement).all()

        result = []
        for c in conversations:
            # Get first bot message as summary
            msg_statement = (
                select(Message)
                .where(Message.conversation_id == c.id)
                .where(Message.role == "bot")
                .order_by(Message.id)
            )
            first_bot_msg = session.exec(msg_statement).first()
            summary = ""
            if first_bot_msg:
                # Extract first 200 characters, truncate if needed
                summary = first_bot_msg.content[:200]
                if len(first_bot_msg.content) > 200:
                    summary += "..."
            
            # Get PDF URL from FileRecord
            file_record = session.exec(
                select(FileRecord).where(FileRecord.conversation_id == c.id)
            ).first()
            pdf_url = file_record.poe_url if file_record else None
            
            result.append({
                "id": c.id,
                "title": c.title,
                "created_at": c.created_at,
                "summary": summary,
                "pdf_url": pdf_url
            })
        
        return result


# Static file serving

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_root():
    return FileResponse("static/index.html")

@app.get("/chat")
async def serve_chat_root():
    return FileResponse("static/index.html")

@app.get("/chat/{conversation_id}")
async def serve_chat(conversation_id: str):
    return FileResponse("static/index.html")


def is_valid_title(text: str) -> bool:
    text = text.strip()

    # 太短不要
    if len(text) < 10:
        return False

    # 过滤 arXiv 行
    if text.lower().startswith("arxiv"):
        return False

    # 过滤日期
    if re.search(r"\d{4}", text) and "arxiv" in text.lower():
        return False

    # 过滤分类标签
    if re.search(r"\[.*?\]", text):
        return False

    # 过滤明显作者邮箱
    if "@" in text:
        return False

    return True
