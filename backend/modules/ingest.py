from __future__ import annotations

import hashlib
import io
import tempfile
import uuid
from pathlib import Path
from typing import Optional

import fastapi_poe as fp
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter
from sqlmodel import Session, select

from ..app.dependencies import check_read_only, get_api_key
from ..domain.message_payloads import build_initial_translation_prompt, preprocess_bot_reply_for_storage
from ..platform.config import engine, settings
from ..platform.gateways.poe import extract_title_from_pdf, get_bot_response, upload_file
from ..platform.models import Conversation, FileRecord
from ..platform.task_runtime import enqueue_task, mark_task_progress, register_task_definition, update_task_record
from .assets import extract_and_store_figures, extract_and_store_tables
from .conversations import build_conversation_detail, create_message_pair, get_semantic_result, serialize_semantic_result
from .metadata import extract_and_store_tags, refresh_conversation_semantic_result

router = APIRouter(tags=["ingest"])
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TASK_UPLOAD_DIR = PROJECT_ROOT / "_temp" / "task_uploads"


class IngestPdfTaskPayload(BaseModel):
    upload_path: str
    filename: str
    poe_model: str
    title_model: str
    tag_model: str
    extract_tags: bool = False
    api_key: str


def find_existing_file(session: Session, fingerprint: str) -> Optional[FileRecord]:
    matched_files = session.exec(
        select(FileRecord)
        .where(FileRecord.fingerprint == fingerprint)
        .order_by(FileRecord.uploaded_at.desc())
    ).all()
    stale_files: list[FileRecord] = []
    for file_record in matched_files:
        if session.get(Conversation, file_record.conversation_id):
            return file_record
        stale_files.append(file_record)
    if stale_files:
        for stale_file in stale_files:
            session.delete(stale_file)
        session.commit()
    return None


def create_conversation_shell(
    session: Session,
    conversation_id: str,
    file_id: str,
    original_filename: str,
    fingerprint: str,
    attachment,
) -> None:
    session.add(Conversation(id=conversation_id, title=original_filename, original_filename=original_filename))
    session.add(
        FileRecord(
            id=file_id,
            conversation_id=conversation_id,
            filename=original_filename,
            fingerprint=fingerprint,
            poe_url=attachment.url,
            content_type=attachment.content_type,
            poe_name=attachment.name,
        )
    )
    session.commit()


def update_conversation_title(session: Session, conversation_id: str, title: str) -> Optional[Conversation]:
    conversation = session.get(Conversation, conversation_id)
    if not conversation:
        return None
    conversation.title = title
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def queue_ingest_pdf(
    *,
    filename: str,
    poe_model: str,
    title_model: str,
    tag_model: str,
    extract_tags: bool,
    api_key: str,
    file_bytes: bytes,
) -> dict:
    TASK_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    staged_path = TASK_UPLOAD_DIR / f"{uuid.uuid4().hex}.pdf"
    staged_path.write_bytes(file_bytes)
    payload = IngestPdfTaskPayload(
        upload_path=str(staged_path),
        filename=filename,
        poe_model=poe_model,
        title_model=title_model,
        tag_model=tag_model,
        extract_tags=extract_tags,
        api_key=api_key,
    )
    return enqueue_task("ingest_pdf", payload)


async def handle_ingest_task(task_id: str, payload: IngestPdfTaskPayload) -> dict:
    upload_path = Path(payload.upload_path)
    if not payload.api_key:
        raise HTTPException(status_code=400, detail="API Key is required.")
    try:
        mark_task_progress(task_id, "读取上传文件")
        if not upload_path.exists():
            raise RuntimeError("Uploaded file staging path does not exist.")
        file_bytes = upload_path.read_bytes()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")
        fingerprint = hashlib.sha256(file_bytes).hexdigest()
        mark_task_progress(task_id, "检查重复文件")
        with Session(engine) as session:
            existing_file = find_existing_file(session, fingerprint)
            if existing_file:
                update_task_record(task_id, conversation_id=existing_file.conversation_id)
                detail = build_conversation_detail(session, existing_file.conversation_id)
                return {
                    "conversation_id": existing_file.conversation_id,
                    "title": detail.title,
                    "messages": [message.model_dump() for message in detail.messages],
                    "exists": True,
                    "pdf_url": detail.pdf_url,
                    "figures": [figure.model_dump() for figure in detail.figures],
                    "tables": [table.model_dump() for table in detail.tables],
                    "tags": [tag.model_dump() for tag in detail.tags],
                    **serialize_semantic_result(get_semantic_result(session, existing_file.conversation_id)).model_dump(),
                }

        conversation_id = uuid.uuid4().hex[:12]
        file_id = uuid.uuid4().hex
        mark_task_progress(task_id, "上传原始 PDF 到 Poe")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            with open(tmp.name, "rb") as file_obj:
                pdf_attachment = await upload_file(file_obj, payload.api_key, payload.filename)

        mark_task_progress(task_id, "创建会话壳")
        with Session(engine) as session:
            create_conversation_shell(session, conversation_id, file_id, payload.filename, fingerprint, pdf_attachment)
        update_task_record(task_id, conversation_id=conversation_id)

        title_extraction_attachment = pdf_attachment
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            if len(reader.pages) > 0:
                writer = PdfWriter()
                writer.add_page(reader.pages[0])
                first_page_pdf_bytes = io.BytesIO()
                writer.write(first_page_pdf_bytes)
                first_page_pdf_bytes.seek(0)
                with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_first_page:
                    tmp_first_page.write(first_page_pdf_bytes.getvalue())
                    tmp_first_page.flush()
                    with open(tmp_first_page.name, "rb") as file_obj:
                        title_extraction_attachment = await upload_file(file_obj, payload.api_key, f"first_page_{payload.filename}")
        except Exception as exc:
            print(f"Error processing PDF for title extraction: {exc}")

        mark_task_progress(task_id, "提取论文标题")
        extracted_title = await extract_title_from_pdf(title_extraction_attachment, payload.api_key, payload.title_model)
        final_title = extracted_title or payload.filename
        with Session(engine) as session:
            if update_conversation_title(session, conversation_id, final_title) is None:
                raise RuntimeError(f"Failed to update conversation title for {conversation_id}.")

        mark_task_progress(task_id, "生成首轮翻译")
        initial_prompt = build_initial_translation_prompt(settings.initial_prompt)
        response_text = await get_bot_response(
            [fp.ProtocolMessage(role="user", content=initial_prompt, attachments=[pdf_attachment])],
            payload.poe_model,
            payload.api_key,
        )
        prepared_response = preprocess_bot_reply_for_storage(response_text)
        response_content = str(prepared_response["content"])
        with Session(engine) as session:
            create_message_pair(
                session,
                conversation_id,
                initial_prompt,
                response_text,
                user_message_kind="system_prompt",
                user_visible_to_user=False,
                bot_section_category=None,
                bot_client_payload=prepared_response["client_payload"],
            )
            mark_task_progress(task_id, "提取论文插图")
            extract_and_store_figures(session, conversation_id, file_bytes)
            mark_task_progress(task_id, "提取论文表格")
            extract_and_store_tables(session, conversation_id, file_bytes)
            mark_task_progress(task_id, "提取论文标签")
            if payload.extract_tags:
                await extract_and_store_tags(session, conversation_id, final_title, response_content, payload.tag_model, payload.api_key)
            mark_task_progress(task_id, "刷新 Semantic Scholar 元数据")
            semantic_result = refresh_conversation_semantic_result(session, conversation_id, final_title)
            detail = build_conversation_detail(session, conversation_id)
            return {
                "conversation_id": conversation_id,
                "title": final_title,
                "messages": [message.model_dump() for message in detail.messages],
                "translation_status": prepared_response["translation_status"],
                "content": response_content,
                "display_reply": response_content,
                "pdf_url": pdf_attachment.url,
                "figures": [figure.model_dump() for figure in detail.figures],
                "tables": [table.model_dump() for table in detail.tables],
                "tags": [tag.model_dump() for tag in detail.tags],
                **serialize_semantic_result(semantic_result).model_dump(),
            }
    finally:
        if upload_path.exists():
            upload_path.unlink()


async def validate_upload(file: UploadFile) -> bytes:
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return file_bytes


register_task_definition("ingest_pdf", IngestPdfTaskPayload, handle_ingest_task)


@router.post("/tasks/ingest-pdf")
async def ingest_pdf_route(
    file: UploadFile = File(...),
    poe_model: str = Form(default=settings.poe_model),
    title_model: str = Form(default=settings.poe_model),
    tag_model: str = Form(default=settings.poe_model),
    extract_tags: bool = Form(default=False),
    api_key: str = Depends(get_api_key),
    _read_only: None = Depends(check_read_only),
):
    file_bytes = await validate_upload(file)
    return queue_ingest_pdf(
        filename=(file.filename or "").strip(),
        poe_model=poe_model,
        title_model=title_model,
        tag_model=tag_model,
        extract_tags=extract_tags,
        api_key=api_key,
        file_bytes=file_bytes,
    )
