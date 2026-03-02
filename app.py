import uuid
import re
import tempfile
import hashlib
import fitz  # pymupdf
from datetime import datetime
from typing import Optional

import fastapi_poe as fp
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import SQLModel, Field, create_engine, Session, select


# Database configuration
DATABASE_URL = "sqlite:///translations.db"
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

    # Poe CDN attachment info    poe_url: Optional[str] = None
    content_type: Optional[str] = None
    poe_name: Optional[str] = None

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
            # collect existing bot messages for frontend display and navigation
            stmt = (
                select(Message)
                .where(Message.conversation_id == existing.conversation_id)
                .order_by(Message.id)
            )
            msgs = session.exec(stmt).all()
            # only return bot messages; user messages are not needed by frontend
            bot_messages = [ {"role": "bot", "content": m.content} for m in msgs if m.role == "bot" ]

            return {
                "conversation_id": existing.conversation_id,
                "messages": bot_messages,
                "exists": True
            }

    conversation_id = uuid.uuid4().hex[:12]
    file_id = uuid.uuid4().hex

    # write bytes to a temp file for libraries like fitz requiring a path
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_bytes)
        tmp.flush()
        temp_path = tmp.name

        # upload to Poe CDN once using the temporary file
        with open(temp_path, "rb") as f:
            pdf_attachment = await fp.upload_file(f, api_key=api_key, file_name=file.filename)

    initial_prompt = """
翻译这篇论文，每次翻译一章（摘要单独算一章）。
摘要、章节用 1 级标题，子章节为 2 级标题。
当我说“继续”时翻译下一章，直到结束。
请先翻译摘要。
"""

    message = fp.ProtocolMessage(
        role="user",
        content=initial_prompt,
        attachments=[pdf_attachment]
    )

    response_text = ""

    async for partial in fp.get_bot_response(
        messages=[message],
        bot_name="GPT-5.2-Instant",
        api_key=api_key
    ):
        response_text += partial.text

    # persist records to database
    with Session(engine) as session:

        extracted_title = extract_pdf_title(temp_path)

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

    # return bot-only message array (frontend only needs bot content)
    return {
        "conversation_id": conversation_id,
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
        bot_name="GPT-5.2-Instant",
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

        return {
            "id": conversation.id,
            "title": conversation.title,
            "created_at": conversation.created_at,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in messages
            ]
        }


# List conversations

@app.get("/conversations")
async def list_conversations():
    with Session(engine) as session:
        statement = select(Conversation).order_by(Conversation.created_at.desc())
        conversations = session.exec(statement).all()

        return [
            {
                "id": c.id,
                "title": c.title,
                "created_at": c.created_at
            }
            for c in conversations
        ]


# Static file serving

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_root():
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


def extract_pdf_title(pdf_path: str) -> Optional[str]:
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]

        blocks = page.get_text("dict")["blocks"]

        candidates = []

        for block in blocks:
            if "lines" not in block:
                continue

            for line in block["lines"]:
                line_text = ""
                max_font_size = 0

                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue

                    line_text += text + " "
                    max_font_size = max(max_font_size, span["size"])

                line_text = line_text.strip()

                if is_valid_title(line_text):
                    candidates.append((max_font_size, line_text))

        if not candidates:
            return None

        # 按字体大小排序
        candidates.sort(reverse=True, key=lambda x: x[0])

        return candidates[0][1].strip()

    except Exception as e:
        print("Title extraction error:", e)
        return None
