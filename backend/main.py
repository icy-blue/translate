from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

import fastapi_poe as fp
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import inspect, or_, text
from pypdf import PdfReader, PdfWriter
from sqlmodel import Session, select, func

from . import crud
from .config import settings
from .database import engine
from .dependencies import get_db_session, check_read_only, get_api_key, get_agent_ingest_token
from .models import (
    SQLModel,
    Conversation,
    Message,
    PaperFigure,
    PaperTable,
    PaperTag,
    PaperSemanticScholarResult,
)
from .paper_tags import build_tag_payloads, extract_abstract_for_tagging, get_tag_definition, get_tag_library_payload
from .pdf_figures import extract_pdf_figures, extract_pdf_tables
from .poe_utils import classify_paper_tags, extract_title_from_pdf, get_bot_response, upload_file
from .semantic_scholar import safe_refresh_semantic_scholar_result

app = FastAPI()
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = PROJECT_ROOT / "static"
JOB_UPLOAD_DIR = PROJECT_ROOT / "_temp" / "job_uploads"
LOCAL_TIMEZONE = datetime.now().astimezone().tzinfo or timezone.utc
LOCAL_TIMEZONE_OFFSET = datetime.now().astimezone().strftime("%z")
JOB_QUEUE: asyncio.Queue[str] = asyncio.Queue()
JOB_WORKERS: list[asyncio.Task] = []
SESSION_ENQUEUE_LOCKS: dict[str, asyncio.Lock] = {}
SESSION_ENQUEUE_LOCKS_GUARD = asyncio.Lock()


class PipelineMessagePayload(BaseModel):
    role: str
    content: str


class PipelineAssetPayload(BaseModel):
    page_number: int = 1
    caption: str = ""
    image_mime_type: str = "image/webp"
    image_data_base64: str | None = None
    image_data: str | None = None
    image_width: int = 1
    image_height: int = 1
    figure_index: int | None = None
    figure_label: str | None = None
    table_index: int | None = None
    table_label: str | None = None


class PipelineTagPayload(BaseModel):
    category_code: str = ""
    category_label: str = ""
    tag_code: str
    tag_label: str = ""
    tag_path: str = ""
    source: str = "agent"


class PipelineMetaPayload(BaseModel):
    status: str | None = None
    paper_id: str | None = None
    corpus_id: int | None = None
    matched_title: str | None = None
    url: str | None = None
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    venue_abbr: str = ""
    ccf_category: str = "None"
    ccf_type: str = "None"
    publication_date: str | None = None
    is_open_access: bool | None = None
    match_score: float | None = None
    citation_count: int | None = None
    reference_count: int | None = None
    authors_json: str | None = None
    external_ids_json: str | None = None
    publication_types_json: str | None = None
    publication_venue_json: str | None = None
    journal_json: str | None = None
    open_access_pdf_json: str | None = None
    raw_response_json: str | None = None
    raw_response: dict[str, Any] | None = None
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
    conversation_id: str | None = None
    title: str
    file_record: PipelineFileRecordPayload
    messages: list[PipelineMessagePayload] = Field(default_factory=list)
    figures: list[PipelineAssetPayload] = Field(default_factory=list)
    tables: list[PipelineAssetPayload] = Field(default_factory=list)
    tags: list[PipelineTagPayload] = Field(default_factory=list)
    meta: PipelineMetaPayload | None = None
    errors: list[PipelineErrorPayload] = Field(default_factory=list)


def _build_postgres_fixed_offset_timezone(offset: str) -> str:
    if not offset:
        return "+00:00"
    sign = "-" if offset.startswith("+") else "+"
    return f"{sign}{offset[1:3]}:{offset[3:]}"


LOCAL_TIMEZONE_SQL = _build_postgres_fixed_offset_timezone(LOCAL_TIMEZONE_OFFSET)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_startup():
    SQLModel.metadata.create_all(engine)
    _ensure_asset_columns()
    _ensure_timestamp_timezone_columns()
    _recover_pending_jobs()
    _start_job_workers()


@app.on_event("shutdown")
async def on_shutdown():
    for worker in JOB_WORKERS:
        worker.cancel()
    if JOB_WORKERS:
        await asyncio.gather(*JOB_WORKERS, return_exceptions=True)
    JOB_WORKERS.clear()

def _safe_json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _serialize_async_job(job) -> dict:
    payload = {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress or "",
        "conversation_id": job.conversation_id,
        "created_at": _ensure_utc_timezone(job.created_at),
        "started_at": _ensure_utc_timezone(job.started_at) if job.started_at else None,
        "finished_at": _ensure_utc_timezone(job.finished_at) if job.finished_at else None,
        "updated_at": _ensure_utc_timezone(job.updated_at),
    }
    if job.status == "succeeded":
        payload["result"] = _safe_json_loads(job.result_json, {})
    if job.status == "failed":
        payload["error_message"] = job.error_message or "任务执行失败。"
    return payload


def _update_async_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: str | None = None,
    result: dict | None = None,
    error_message: str | None = None,
    conversation_id: str | None = None,
    started: bool = False,
    finished: bool = False,
):
    with Session(engine) as session:
        crud.touch_async_job(
            session,
            job_id,
            status=status,
            progress=progress,
            result=result,
            error_message=error_message,
            conversation_id=conversation_id,
            started=started,
            finished=finished,
        )


def _mark_job_progress(job_id: str, progress: str):
    _update_async_job(job_id, progress=progress)


def _recover_pending_jobs():
    pending_job_ids: list[str] = []
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        pending_jobs = crud.list_recoverable_async_jobs(session)
        for job in pending_jobs:
            pending_job_ids.append(job.id)
            if job.status == "running":
                job.status = "queued"
                job.progress = "服务已重启，任务重新排队中"
                job.started_at = None
                job.updated_at = now
                session.add(job)
        session.commit()

    for job_id in pending_job_ids:
        JOB_QUEUE.put_nowait(job_id)


def _start_job_workers():
    if JOB_WORKERS:
        return
    worker_count = max(1, settings.async_job_workers)
    for idx in range(worker_count):
        JOB_WORKERS.append(asyncio.create_task(_job_worker_loop(idx + 1), name=f"async-job-worker-{idx + 1}"))


def _enqueue_async_job(job_type: str, payload: dict, conversation_id: str | None = None) -> dict:
    job_id = uuid.uuid4().hex
    with Session(engine) as session:
        crud.create_async_job(
            session,
            job_id=job_id,
            job_type=job_type,
            payload=payload,
            conversation_id=conversation_id,
        )
    JOB_QUEUE.put_nowait(job_id)
    return {"job_id": job_id, "status": "queued"}


async def _get_session_enqueue_lock(conversation_id: str) -> asyncio.Lock:
    async with SESSION_ENQUEUE_LOCKS_GUARD:
        lock = SESSION_ENQUEUE_LOCKS.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            SESSION_ENQUEUE_LOCKS[conversation_id] = lock
        return lock


async def _job_worker_loop(worker_index: int):
    while True:
        job_id = await JOB_QUEUE.get()
        try:
            await _process_async_job(job_id, worker_index)
        finally:
            JOB_QUEUE.task_done()


async def _process_async_job(job_id: str, worker_index: int):
    with Session(engine) as session:
        job = crud.get_async_job(session, job_id)
        if not job:
            return
        if job.status != "queued":
            return
        payload = _safe_json_loads(job.payload_json, {})
        job_type = job.job_type
        now = datetime.now(timezone.utc)
        job.status = "running"
        job.progress = f"worker-{worker_index} 已开始处理"
        job.error_message = ""
        if job.started_at is None:
            job.started_at = now
        job.updated_at = now
        session.add(job)
        session.commit()

    try:
        if job_type == "upload":
            result = await _run_upload_job(job_id, payload)
        elif job_type in {"continue", "custom_message"}:
            result = await _run_continue_job(job_id, payload)
        else:
            raise RuntimeError(f"Unsupported async job type: {job_type}")

        conversation_id = result.get("conversation_id") if isinstance(result, dict) else None
        _update_async_job(
            job_id,
            status="succeeded",
            progress="任务已完成",
            result=result if isinstance(result, dict) else {"value": result},
            error_message="",
            conversation_id=conversation_id,
            finished=True,
        )
    except HTTPException as exc:
        detail = exc.detail
        if isinstance(detail, str):
            error_message = detail
        else:
            error_message = json.dumps(detail, ensure_ascii=False)
        _update_async_job(
            job_id,
            status="failed",
            progress="任务失败",
            error_message=error_message,
            finished=True,
        )
    except Exception as exc:
        _update_async_job(
            job_id,
            status="failed",
            progress="任务失败",
            error_message=str(exc),
            finished=True,
        )


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


async def _run_upload_job(job_id: str, payload: dict) -> dict:
    _mark_job_progress(job_id, "读取上传文件")
    upload_path = Path(str(payload.get("upload_path", "")).strip())
    filename = str(payload.get("filename", "")).strip() or "upload.pdf"
    poe_model = str(payload.get("poe_model", settings.poe_model))
    title_model = str(payload.get("title_model", settings.poe_model))
    tag_model = str(payload.get("tag_model", settings.poe_model))
    extract_tags = _as_bool(payload.get("extract_tags", False))
    api_key = str(payload.get("api_key", "")).strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key is required.")

    try:
        if not upload_path.exists():
            raise RuntimeError("Uploaded file staging path does not exist.")
        file_bytes = upload_path.read_bytes()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")

        _mark_job_progress(job_id, "计算文件指纹")
        fingerprint = hashlib.sha256(file_bytes).hexdigest()
        _mark_job_progress(job_id, "正在检查是否已存在同指纹会话")

        with Session(engine) as session:
            existing_file = crud.find_existing_file(session, fingerprint)
            if existing_file:
                _update_async_job(job_id, conversation_id=existing_file.conversation_id)
                _mark_job_progress(job_id, "命中已存在会话，加载历史结果")
                conversation = crud.get_conversation(session, existing_file.conversation_id)
                messages = crud.get_messages(session, existing_file.conversation_id)

                figures = crud.get_figures(session, existing_file.conversation_id)
                if not figures:
                    _mark_job_progress(job_id, "会话已有记录，补提取插图")
                    figures = _extract_and_store_figures(session, existing_file.conversation_id, file_bytes)

                tables = crud.get_tables(session, existing_file.conversation_id)
                if not tables:
                    _mark_job_progress(job_id, "会话已有记录，补提取表格")
                    tables = _extract_and_store_tables(session, existing_file.conversation_id, file_bytes)

                tags = crud.get_tags(session, existing_file.conversation_id)
                if extract_tags and not tags and conversation:
                    _mark_job_progress(job_id, "会话已有记录，补提取标签")
                    first_bot_message = next((message.content for message in messages if message.role == "bot"), "")
                    tags = await _extract_and_store_tags(
                        session,
                        existing_file.conversation_id,
                        conversation.title or existing_file.filename,
                        first_bot_message,
                        tag_model,
                        api_key,
                    )

                semantic_result = crud.get_semantic_scholar_result(session, existing_file.conversation_id)
                if semantic_result is None and conversation:
                    _mark_job_progress(job_id, "会话已有记录，补刷新论文元数据")
                    semantic_result = _refresh_conversation_semantic_result(
                        session,
                        existing_file.conversation_id,
                        conversation.title or existing_file.filename,
                    )

                def keep(m):
                    return m.role != "user" or (m.content != settings.initial_prompt and m.content != "继续")

                response = {
                    "conversation_id": existing_file.conversation_id,
                    "title": conversation.title if conversation else None,
                    "messages": [{"role": m.role, "content": m.content} for m in messages if keep(m)],
                    "exists": True,
                    "pdf_url": existing_file.poe_url,
                    "figures": _serialize_figures(figures),
                    "tables": _serialize_tables(tables),
                    "tags": _serialize_tags(tags),
                }
                response.update(_serialize_semantic_result(semantic_result))
                return response

        conversation_id = uuid.uuid4().hex[:12]
        file_id = uuid.uuid4().hex

        _mark_job_progress(job_id, "上传原始 PDF 到 Poe")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            with open(tmp.name, "rb") as f:
                pdf_attachment = await upload_file(f, api_key, filename)

        _mark_job_progress(job_id, "创建会话（可提前进入聊天页）")
        with Session(engine) as session:
            crud.create_conversation_shell(
                session=session,
                conversation_id=conversation_id,
                file_id=file_id,
                original_filename=filename,
                fingerprint=fingerprint,
                attachment=pdf_attachment,
            )
        _update_async_job(job_id, conversation_id=conversation_id)

        _mark_job_progress(job_id, "准备首页 PDF（用于标题提取）")
        title_extraction_attachment = None
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
                    with open(tmp_first_page.name, "rb") as f:
                        _mark_job_progress(job_id, "上传首页 PDF 到 Poe")
                        title_extraction_attachment = await upload_file(f, api_key, f"first_page_{filename}")
        except Exception as exc:
            print(f"Error processing PDF for title extraction: {exc}")
            title_extraction_attachment = pdf_attachment

        _mark_job_progress(job_id, "调用标题模型提取论文标题")
        extracted_title = await extract_title_from_pdf(
            title_extraction_attachment or pdf_attachment,
            api_key,
            title_model,
        )
        final_title = extracted_title or filename
        with Session(engine) as session:
            updated_conversation = crud.update_conversation_title(session, conversation_id, final_title)
            if updated_conversation is None:
                raise RuntimeError(f"Failed to update conversation title for {conversation_id}.")
        _mark_job_progress(job_id, f"标题已提取：{final_title}")

        _mark_job_progress(job_id, "调用翻译模型生成摘要/首章")
        initial_prompt = settings.initial_prompt
        message = fp.ProtocolMessage(role="user", content=initial_prompt, attachments=[pdf_attachment])
        response_text = await get_bot_response([message], poe_model, api_key)

        _mark_job_progress(job_id, "写入首轮翻译消息")
        response_payload: dict | None = None
        with Session(engine) as session:
            crud.create_messages(session, conversation_id, initial_prompt, response_text)
            _mark_job_progress(job_id, "提取论文插图")
            figures = _extract_and_store_figures(session, conversation_id, file_bytes)
            _mark_job_progress(job_id, "提取论文表格")
            tables = _extract_and_store_tables(session, conversation_id, file_bytes)
            _mark_job_progress(job_id, "提取论文标签")
            tags = (
                await _extract_and_store_tags(session, conversation_id, final_title, response_text, tag_model, api_key)
                if extract_tags
                else []
            )
            _mark_job_progress(job_id, "刷新 Semantic Scholar 元数据")
            semantic_result = _refresh_conversation_semantic_result(session, conversation_id, final_title)
            # Serialize ORM objects before session closes, avoiding detached-instance refresh errors.
            response_payload = {
                "conversation_id": conversation_id,
                "title": final_title,
                "messages": [{"role": "bot", "content": response_text}],
                "pdf_url": pdf_attachment.url,
                "figures": _serialize_figures(figures),
                "tables": _serialize_tables(tables),
                "tags": _serialize_tags(tags),
            }
            response_payload.update(_serialize_semantic_result(semantic_result))

        return response_payload or {}
    finally:
        if upload_path.exists():
            upload_path.unlink()


async def _run_continue_job(job_id: str, payload: dict) -> dict:
    conversation_id = str(payload.get("conversation_id", "")).strip()
    new_user_message = str(payload.get("new_user_message", "")).strip()
    poe_model = str(payload.get("poe_model", settings.poe_model))
    api_key = str(payload.get("api_key", "")).strip()
    save_to_record = _as_bool(payload.get("save_to_record", True))

    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required.")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key is required.")
    if not new_user_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    _mark_job_progress(job_id, "加载会话上下文")
    with Session(engine) as session:
        response = await _continue_conversation(
            conversation_id=conversation_id,
            new_user_message=new_user_message,
            poe_model=poe_model,
            api_key=api_key,
            session=session,
            save_to_record=save_to_record,
            progress_callback=lambda p: _mark_job_progress(job_id, p),
        )
    _mark_job_progress(job_id, "整理返回结果")
    return {"conversation_id": conversation_id, **response}


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str, session: Session = Depends(get_db_session)):
    job = crud.get_async_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    payload = _serialize_async_job(job)
    if job.conversation_id:
        conversation = crud.get_conversation(session, job.conversation_id)
        if conversation and conversation.title:
            payload["conversation_title"] = conversation.title
    return payload


# Endpoint to handle PDF uploads and start the translation process
@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    poe_model: str = Form(default=settings.poe_model),
    title_model: str = Form(default=settings.poe_model),
    tag_model: str = Form(default=settings.poe_model),
    extract_tags: bool = Form(default=False),
    api_key: str = Depends(get_api_key),
    _read_only: None = Depends(check_read_only),
):
    filename = (file.filename or "").strip()
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    JOB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    staged_path = JOB_UPLOAD_DIR / f"{uuid.uuid4().hex}.pdf"
    staged_path.write_bytes(file_bytes)

    return _enqueue_async_job(
        "upload",
        {
            "upload_path": str(staged_path),
            "filename": filename,
            "poe_model": poe_model,
            "title_model": title_model,
            "tag_model": tag_model,
            "extract_tags": extract_tags,
            "api_key": api_key,
        },
    )

# Common logic for continuing a conversation
async def _continue_conversation(
    conversation_id: str,
    new_user_message: str,
    poe_model: str,
    api_key: str,
    session: Session,
    save_to_record: bool,
    progress_callback=None,
):
    if progress_callback:
        progress_callback("校验会话与文件")
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    file_record = crud.get_file_record(session, conversation_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    if progress_callback:
        progress_callback("读取历史消息")
    db_messages = crud.get_messages(session, conversation_id)
    pdf_attachment = fp.Attachment(url=file_record.poe_url, content_type=file_record.content_type, name=file_record.poe_name)

    if progress_callback:
        progress_callback("构建 Poe 请求")
    poe_messages = [
        fp.ProtocolMessage(role="user", content=m.content, attachments=[pdf_attachment]) if i == 0 and m.role == "user" else fp.ProtocolMessage(role=m.role, content=m.content)
        for i, m in enumerate(db_messages)
    ]
    poe_messages.append(fp.ProtocolMessage(role="user", content=new_user_message))

    if progress_callback:
        progress_callback("等待 Poe 返回翻译结果")
    response_text = await get_bot_response(poe_messages, poe_model, api_key)

    if save_to_record:
        if progress_callback:
            progress_callback("写入会话消息到数据库")
        crud.create_messages(session, conversation_id, new_user_message, response_text)

    if progress_callback:
        progress_callback("翻译结果已生成")
    return {"reply": response_text}

# Endpoint to continue an existing translation
@app.post("/continue/{conversation_id}")
async def continue_translation(
    conversation_id: str,
    poe_model: str = Form(default=settings.poe_model),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only)
):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    enqueue_lock = await _get_session_enqueue_lock(conversation_id)
    async with enqueue_lock:
        active_job = crud.get_active_translation_job(session, conversation_id)
        if active_job:
            raise HTTPException(
                status_code=409,
                detail=f"会话已有翻译任务进行中（job_id={active_job.id}，状态={active_job.status}）。请等待完成后再继续。",
            )
        return _enqueue_async_job(
            "continue",
            {
                "conversation_id": conversation_id,
                "new_user_message": "继续",
                "poe_model": poe_model,
                "api_key": api_key,
                "save_to_record": True,
            },
            conversation_id=conversation_id,
        )

# Endpoint for sending a custom message in a conversation
@app.post("/custom_message/{conversation_id}")
async def custom_message(
    conversation_id: str,
    message: str = Form(...),
    save_to_record: bool = Form(...),
    poe_model: str = Form(default=settings.poe_model),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only)
):
    if not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    enqueue_lock = await _get_session_enqueue_lock(conversation_id)
    async with enqueue_lock:
        active_job = crud.get_active_translation_job(session, conversation_id)
        if active_job:
            raise HTTPException(
                status_code=409,
                detail=f"会话已有翻译任务进行中（job_id={active_job.id}，状态={active_job.status}）。请等待完成后再发送新消息。",
            )
        return _enqueue_async_job(
            "custom_message",
            {
                "conversation_id": conversation_id,
                "new_user_message": message,
                "poe_model": poe_model,
                "api_key": api_key,
                "save_to_record": save_to_record,
            },
            conversation_id=conversation_id,
        )

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
    semantic_result = crud.get_semantic_scholar_result(session, conversation_id)
    pdf_url = file_record.poe_url if file_record else None

    def keep(m):
        return m.role != "user" or (m.content.replace('\n', '') != settings.initial_prompt and m.content != "继续")

    response = {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": _ensure_utc_timezone(conversation.created_at),
        "messages": [{"role": m.role, "content": m.content} for m in messages if keep(m)],
        "pdf_url": pdf_url,
        "figures": _serialize_figures(figures),
        "tables": _serialize_tables(tables),
        "tags": _serialize_tags(tags),
    }
    response.update(_serialize_semantic_result(semantic_result))
    return response


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
    return dt.replace(tzinfo=LOCAL_TIMEZONE) if dt.tzinfo is None else dt.astimezone(LOCAL_TIMEZONE)

# Helper to build a conversation data object for API responses
def _build_conversation_data(session: Session, conversation: Conversation, include_relevance: bool = False, relevance_score: int = 0) -> dict:
    return _build_conversation_data_with_semantic(
        session,
        conversation,
        crud.get_semantic_scholar_result(session, conversation.id),
        include_relevance,
        relevance_score,
    )


def _build_conversation_data_with_semantic(
    session: Session,
    conversation: Conversation,
    semantic_result,
    include_relevance: bool = False,
    relevance_score: int = 0,
) -> dict:
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
    result.update(_serialize_semantic_result(semantic_result))
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


def _refresh_conversation_semantic_result(
    session: Session,
    conversation_id: str,
    title: str,
):
    return safe_refresh_semantic_scholar_result(
        session=session,
        conversation_id=conversation_id,
        title=title,
    )


async def _refresh_conversation_annotations(
    session: Session,
    conversation_id: str,
    title: str,
    first_bot_message: str,
    tag_model: str,
    api_key: str,
):
    tags = await _extract_and_store_tags(
        session,
        conversation_id,
        title,
        first_bot_message,
        tag_model,
        api_key,
    )
    semantic_result = _refresh_conversation_semantic_result(session, conversation_id, title)
    return tags, semantic_result


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


def _ensure_timestamp_timezone_columns():
    if engine.dialect.name != "postgresql":
        return

    target_columns = {
        "paperfigure": ["created_at"],
        "papertable": ["created_at"],
        "papertag": ["created_at"],
        "papersemanticscholarresult": ["created_at", "updated_at"],
    }

    with engine.begin() as connection:
        for table_name, column_names in target_columns.items():
            for column_name in column_names:
                data_type = connection.execute(
                    text(
                        """
                        SELECT data_type
                        FROM information_schema.columns
                        WHERE table_name = :table_name AND column_name = :column_name
                        """
                    ),
                    {"table_name": table_name, "column_name": column_name},
                ).scalar()
                if data_type is None:
                    continue
                if data_type == "timestamp with time zone":
                    continue
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ALTER COLUMN {column_name} TYPE TIMESTAMPTZ "
                        f"USING {column_name} AT TIME ZONE '{LOCAL_TIMEZONE_SQL}'"
                    )
                )


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
    semantic_map = crud.get_semantic_scholar_results_map(session, [conv.id for conv in conversations])
    return [
        _build_conversation_data_with_semantic(
            session,
            conv,
            semantic_map.get(conv.id),
            include_relevance,
            relevance_scores[i],
        )
        for i, conv in enumerate(conversations)
    ]


def _serialize_semantic_result(semantic_result) -> dict:
    if semantic_result is None:
        return {
            "venue_abbr": "",
            "ccf_category": "None",
            "ccf_type": "None",
            "citation_count": None,
            "venue": None,
            "year": None,
            "semantic_updated_at": None,
        }
    return {
        "venue_abbr": semantic_result.venue_abbr or "",
        "ccf_category": semantic_result.ccf_category or "None",
        "ccf_type": semantic_result.ccf_type or "None",
        "citation_count": semantic_result.citation_count,
        "venue": semantic_result.venue,
        "year": semantic_result.year,
        "semantic_updated_at": _ensure_utc_timezone(semantic_result.updated_at),
    }


def _normalize_string_filters(values: Optional[List[str]]) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        candidate = value.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def _normalize_year_filters(values: Optional[List[str]]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            year = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if year in seen:
            continue
        seen.add(year)
        normalized.append(year)
    return normalized


def _build_filtered_conversation_statement(
    tag_codes: Optional[List[str]] = None,
    ccf_categories: Optional[List[str]] = None,
    venue_filters: Optional[List[str]] = None,
    years: Optional[List[int]] = None,
):
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

    normalized_ccf_categories = _normalize_string_filters(ccf_categories)
    if normalized_ccf_categories:
        semantic_ids = select(PaperSemanticScholarResult.conversation_id)
        ccf_conditions = []
        real_categories = [value for value in normalized_ccf_categories if value in {"A", "B", "C"}]
        if real_categories:
            ccf_conditions.append(
                Conversation.id.in_(
                    select(PaperSemanticScholarResult.conversation_id).where(
                        PaperSemanticScholarResult.ccf_category.in_(real_categories)
                    )
                )
            )
        if "None" in normalized_ccf_categories:
            ccf_conditions.append(
                Conversation.id.in_(
                    select(PaperSemanticScholarResult.conversation_id).where(
                        PaperSemanticScholarResult.ccf_category == "None"
                    )
                )
            )
            ccf_conditions.append(~Conversation.id.in_(semantic_ids))
        if ccf_conditions:
            statement = statement.where(or_(*ccf_conditions))

    normalized_venues = _normalize_string_filters(venue_filters)
    if normalized_venues:
        statement = statement.where(
            Conversation.id.in_(
                select(PaperSemanticScholarResult.conversation_id).where(
                    or_(
                        PaperSemanticScholarResult.venue_abbr.in_(normalized_venues),
                        PaperSemanticScholarResult.venue.in_(normalized_venues),
                    )
                )
            )
        )

    normalized_years = [year for year in (years or []) if isinstance(year, int)]
    if normalized_years:
        statement = statement.where(
            Conversation.id.in_(
                select(PaperSemanticScholarResult.conversation_id).where(
                    PaperSemanticScholarResult.year.in_(normalized_years)
                )
            )
        )

    return statement


def _count_filtered_conversations(
    session: Session,
    tag_codes: Optional[List[str]] = None,
    ccf_categories: Optional[List[str]] = None,
    venue_filters: Optional[List[str]] = None,
    years: Optional[List[int]] = None,
) -> int:
    filtered_statement = _build_filtered_conversation_statement(
        tag_codes=tag_codes,
        ccf_categories=ccf_categories,
        venue_filters=venue_filters,
        years=years,
    )
    count_statement = select(func.count()).select_from(filtered_statement.subquery())
    return session.exec(count_statement).one()


def _build_search_filter_payload(session: Session) -> dict:
    total_conversations = session.exec(select(func.count(Conversation.id))).one()
    ccf_counts = {
        category: count
        for category, count in session.exec(
            select(
                PaperSemanticScholarResult.ccf_category,
                func.count(PaperSemanticScholarResult.conversation_id),
            ).group_by(PaperSemanticScholarResult.ccf_category)
        ).all()
    }
    ccf_known_count = sum(ccf_counts.get(category, 0) for category in ("A", "B", "C"))
    ccf_none_count = max(0, total_conversations - ccf_known_count)

    venue_rows = session.exec(
        select(
            PaperSemanticScholarResult.venue_abbr,
            PaperSemanticScholarResult.venue,
        ).where(
            or_(
                PaperSemanticScholarResult.venue_abbr != "",
                PaperSemanticScholarResult.venue.is_not(None),
            )
        )
    ).all()
    venue_counts: dict[str, dict] = {}
    for venue_abbr, venue in venue_rows:
        value = venue_abbr or venue
        if not value:
            continue
        entry = venue_counts.setdefault(
            value,
            {
                "value": value,
                "label": venue_abbr or venue,
                "full_label": venue or venue_abbr or value,
                "count": 0,
            },
        )
        entry["count"] += 1

    year_counts = session.exec(
        select(
            PaperSemanticScholarResult.year,
            func.count(PaperSemanticScholarResult.conversation_id),
        )
        .where(PaperSemanticScholarResult.year.is_not(None))
        .group_by(PaperSemanticScholarResult.year)
        .order_by(PaperSemanticScholarResult.year.desc())
    ).all()

    return {
        "total_conversations": total_conversations,
        "ccf_categories": [
            {"value": "A", "label": "CCF-A", "count": ccf_counts.get("A", 0)},
            {"value": "B", "label": "CCF-B", "count": ccf_counts.get("B", 0)},
            {"value": "C", "label": "CCF-C", "count": ccf_counts.get("C", 0)},
            {"value": "None", "label": "CCF-None", "count": ccf_none_count},
        ],
        "venues": sorted(venue_counts.values(), key=lambda item: item["label"].lower()),
        "years": [
            {"value": str(year), "label": str(year), "count": count}
            for year, count in year_counts
            if year is not None
        ],
    }


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
    ccf_category: Optional[List[str]] = Query(default=None),
    venue_filter: Optional[List[str]] = Query(default=None),
    year: Optional[List[str]] = Query(default=None),
    session: Session = Depends(get_db_session),
):
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    normalized_tag_codes = _normalize_tag_codes(tag_code)
    normalized_ccf_categories = _normalize_string_filters(ccf_category)
    normalized_venue_filters = _normalize_string_filters(venue_filter)
    normalized_years = _normalize_year_filters(year)

    total = _count_filtered_conversations(
        session,
        tag_codes=normalized_tag_codes,
        ccf_categories=normalized_ccf_categories,
        venue_filters=normalized_venue_filters,
        years=normalized_years,
    )
    conversations_statement = _build_filtered_conversation_statement(
        tag_codes=normalized_tag_codes,
        ccf_categories=normalized_ccf_categories,
        venue_filters=normalized_venue_filters,
        years=normalized_years,
    ).order_by(Conversation.created_at.desc())
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
    ccf_category: Optional[List[str]] = Query(default=None),
    venue_filter: Optional[List[str]] = Query(default=None),
    year: Optional[List[str]] = Query(default=None),
    session: Session = Depends(get_db_session),
):
    normalized_tag_codes = _normalize_tag_codes(tag_code)
    normalized_ccf_categories = _normalize_string_filters(ccf_category)
    normalized_venue_filters = _normalize_string_filters(venue_filter)
    normalized_years = _normalize_year_filters(year)
    total_conversations = session.exec(select(func.count(Conversation.id))).one()
    if not (q and q.strip()) and not normalized_tag_codes and not normalized_ccf_categories and not normalized_venue_filters and not normalized_years:
        return {"exact_matches": [], "fuzzy_matches": [], "total_conversations": total_conversations}

    query = q.strip()
    base_statement = _build_filtered_conversation_statement(
        tag_codes=normalized_tag_codes,
        ccf_categories=normalized_ccf_categories,
        venue_filters=normalized_venue_filters,
        years=normalized_years,
    )
    
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
        "fuzzy_matches": fuzzy_matches if search_type != "exact" else [],
        "total_conversations": total_conversations,
    }


@app.get("/tags/library")
async def get_tag_library(session: Session = Depends(get_db_session)):
    return {"categories": get_tag_library_payload(_get_tag_usage_counts(session))}


@app.get("/search/filters")
async def get_search_filters(session: Session = Depends(get_db_session)):
    return _build_search_filter_payload(session)


@app.post("/conversation/{conversation_id}/refresh_metadata")
async def refresh_conversation_metadata(
    conversation_id: str,
    tag_model: str = Form(default=settings.poe_model),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    messages = crud.get_messages(session, conversation_id)
    first_bot_message = next((message.content for message in messages if message.role == "bot"), "")
    tags, semantic_result = await _refresh_conversation_annotations(
        session=session,
        conversation_id=conversation_id,
        title=conversation.title or conversation.original_filename or "",
        first_bot_message=first_bot_message,
        tag_model=tag_model,
        api_key=api_key,
    )
    response = {"tags": _serialize_tags(tags)}
    response.update(_serialize_semantic_result(semantic_result))
    return response


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


@app.post("/agent/pipeline/commit")
async def commit_agent_pipeline_bundle(
    payload: PipelineBundlePayload,
    _agent_token: str = Depends(get_agent_ingest_token),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    try:
        return crud.persist_pipeline_bundle(session, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to persist pipeline bundle: {exc}")

# Static file serving
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
async def serve_root():
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/chat/{path:path}")
async def serve_chat_paths(path: str):
    return FileResponse(STATIC_DIR / "index.html")

@app.get("/chat")
async def serve_chat_root():
    return FileResponse(STATIC_DIR / "index.html")

# System configuration endpoint
@app.get("/config")
async def get_config():
    return {"read_only": settings.read_only, "default_poe_model": settings.poe_model}
