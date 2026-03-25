from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import fastapi_poe as fp
from fastapi import HTTPException
from pypdf import PdfReader, PdfWriter
from sqlmodel import Session

from ..core.config import settings
from ..core.database import engine
from ..integrations.poe import extract_title_from_pdf, get_bot_response, upload_file
from ..persistence import crud
from .annotations import (
    extract_and_store_figures,
    extract_and_store_tables,
    extract_and_store_tags,
    refresh_conversation_semantic_result,
)
from .conversations import continue_conversation
from .message_utils import infer_message_metadata, parse_raw_translation_status_block, safe_json_loads
from .serializers import (
    serialize_async_job,
    serialize_figures,
    serialize_message_record,
    serialize_semantic_result,
    serialize_tables,
    serialize_tags,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOB_UPLOAD_DIR = PROJECT_ROOT / "_temp" / "job_uploads"
JOB_QUEUE: asyncio.Queue[str] = asyncio.Queue()
JOB_WORKERS: list[asyncio.Task] = []
SESSION_ENQUEUE_LOCKS: dict[str, asyncio.Lock] = {}
SESSION_ENQUEUE_LOCKS_GUARD = asyncio.Lock()


def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def update_async_job(
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


def mark_job_progress(job_id: str, progress: str):
    update_async_job(job_id, progress=progress)


def recover_pending_jobs():
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


def start_job_workers():
    if JOB_WORKERS:
        return
    worker_count = max(1, settings.async_job_workers)
    for idx in range(worker_count):
        JOB_WORKERS.append(asyncio.create_task(job_worker_loop(idx + 1), name=f"async-job-worker-{idx + 1}"))


async def stop_job_workers():
    for worker in JOB_WORKERS:
        worker.cancel()
    if JOB_WORKERS:
        await asyncio.gather(*JOB_WORKERS, return_exceptions=True)
    JOB_WORKERS.clear()


def enqueue_async_job(job_type: str, payload: dict, conversation_id: str | None = None) -> dict:
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


async def get_session_enqueue_lock(conversation_id: str) -> asyncio.Lock:
    async with SESSION_ENQUEUE_LOCKS_GUARD:
        lock = SESSION_ENQUEUE_LOCKS.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            SESSION_ENQUEUE_LOCKS[conversation_id] = lock
        return lock


async def job_worker_loop(worker_index: int):
    while True:
        job_id = await JOB_QUEUE.get()
        try:
            await process_async_job(job_id, worker_index)
        finally:
            JOB_QUEUE.task_done()


async def process_async_job(job_id: str, worker_index: int):
    with Session(engine) as session:
        job = crud.get_async_job(session, job_id)
        if not job or job.status != "queued":
            return
        payload = safe_json_loads(job.payload_json, {})
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
            result = await run_upload_job(job_id, payload)
        elif job_type in {"continue", "custom_message"}:
            result = await run_continue_job(job_id, payload, job_type)
        else:
            raise RuntimeError(f"Unsupported async job type: {job_type}")

        conversation_id = result.get("conversation_id") if isinstance(result, dict) else None
        update_async_job(
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
        error_message = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
        update_async_job(
            job_id,
            status="failed",
            progress="任务失败",
            error_message=error_message,
            finished=True,
        )
    except Exception as exc:
        update_async_job(
            job_id,
            status="failed",
            progress="任务失败",
            error_message=str(exc),
            finished=True,
        )


async def run_upload_job(job_id: str, payload: dict) -> dict:
    mark_job_progress(job_id, "读取上传文件")
    upload_path = Path(str(payload.get("upload_path", "")).strip())
    filename = str(payload.get("filename", "")).strip() or "upload.pdf"
    poe_model = str(payload.get("poe_model", settings.poe_model))
    title_model = str(payload.get("title_model", settings.poe_model))
    tag_model = str(payload.get("tag_model", settings.poe_model))
    extract_tags_enabled = as_bool(payload.get("extract_tags", False))
    api_key = str(payload.get("api_key", "")).strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key is required.")

    try:
        if not upload_path.exists():
            raise RuntimeError("Uploaded file staging path does not exist.")
        file_bytes = upload_path.read_bytes()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="The uploaded file is empty.")

        mark_job_progress(job_id, "计算文件指纹")
        fingerprint = hashlib.sha256(file_bytes).hexdigest()
        mark_job_progress(job_id, "正在检查是否已存在同指纹会话")

        with Session(engine) as session:
            existing_file = crud.find_existing_file(session, fingerprint)
            if existing_file:
                update_async_job(job_id, conversation_id=existing_file.conversation_id)
                mark_job_progress(job_id, "命中已存在会话，加载历史结果")
                conversation = crud.get_conversation(session, existing_file.conversation_id)
                messages = crud.get_messages(session, existing_file.conversation_id)

                figures = crud.get_figures(session, existing_file.conversation_id)
                if not figures:
                    mark_job_progress(job_id, "会话已有记录，补提取插图")
                    figures = extract_and_store_figures(session, existing_file.conversation_id, file_bytes)

                tables = crud.get_tables(session, existing_file.conversation_id)
                if not tables:
                    mark_job_progress(job_id, "会话已有记录，补提取表格")
                    tables = extract_and_store_tables(session, existing_file.conversation_id, file_bytes)

                tags = crud.get_tags(session, existing_file.conversation_id)
                if extract_tags_enabled and not tags and conversation:
                    mark_job_progress(job_id, "会话已有记录，补提取标签")
                    first_bot_message = next((message.content for message in messages if infer_message_metadata(message)["role"] == "bot"), "")
                    tags = await extract_and_store_tags(
                        session,
                        existing_file.conversation_id,
                        conversation.title or existing_file.filename,
                        first_bot_message,
                        tag_model,
                        api_key,
                    )

                semantic_result = crud.get_semantic_scholar_result(session, existing_file.conversation_id)
                if semantic_result is None and conversation:
                    mark_job_progress(job_id, "会话已有记录，补刷新论文元数据")
                    semantic_result = refresh_conversation_semantic_result(
                        session,
                        existing_file.conversation_id,
                        conversation.title or existing_file.filename,
                    )

                response = {
                    "conversation_id": existing_file.conversation_id,
                    "title": conversation.title if conversation else None,
                    "messages": [serialize_message_record(message) for message in messages],
                    "exists": True,
                    "pdf_url": existing_file.poe_url,
                    "figures": serialize_figures(figures),
                    "tables": serialize_tables(tables),
                    "tags": serialize_tags(tags),
                }
                response.update(serialize_semantic_result(semantic_result))
                return response

        conversation_id = uuid.uuid4().hex[:12]
        file_id = uuid.uuid4().hex

        mark_job_progress(job_id, "上传原始 PDF 到 Poe")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            with open(tmp.name, "rb") as file_obj:
                pdf_attachment = await upload_file(file_obj, api_key, filename)

        mark_job_progress(job_id, "创建会话（可提前进入聊天页）")
        with Session(engine) as session:
            crud.create_conversation_shell(
                session=session,
                conversation_id=conversation_id,
                file_id=file_id,
                original_filename=filename,
                fingerprint=fingerprint,
                attachment=pdf_attachment,
            )
        update_async_job(job_id, conversation_id=conversation_id)

        mark_job_progress(job_id, "准备首页 PDF（用于标题提取）")
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
                    with open(tmp_first_page.name, "rb") as file_obj:
                        mark_job_progress(job_id, "上传首页 PDF 到 Poe")
                        title_extraction_attachment = await upload_file(file_obj, api_key, f"first_page_{filename}")
        except Exception as exc:
            print(f"Error processing PDF for title extraction: {exc}")
            title_extraction_attachment = pdf_attachment

        mark_job_progress(job_id, "调用标题模型提取论文标题")
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
        mark_job_progress(job_id, f"标题已提取：{final_title}")

        mark_job_progress(job_id, "调用翻译模型生成摘要/首章")
        initial_prompt = settings.initial_prompt
        message = fp.ProtocolMessage(role="user", content=initial_prompt, attachments=[pdf_attachment])
        response_text = await get_bot_response([message], poe_model, api_key)

        mark_job_progress(job_id, "写入首轮翻译消息")
        response_status = parse_raw_translation_status_block(response_text)
        response_client_payload = {"translation_status": response_status} if response_status else None
        response_section_category = None

        with Session(engine) as session:
            crud.create_messages(
                session,
                conversation_id,
                initial_prompt,
                response_text,
                user_message_kind="system_prompt",
                user_visible_to_user=False,
                bot_section_category=response_section_category,
                bot_client_payload=response_client_payload,
            )
            mark_job_progress(job_id, "提取论文插图")
            figures = extract_and_store_figures(session, conversation_id, file_bytes)
            mark_job_progress(job_id, "提取论文表格")
            tables = extract_and_store_tables(session, conversation_id, file_bytes)
            mark_job_progress(job_id, "提取论文标签")
            tags = (
                await extract_and_store_tags(session, conversation_id, final_title, response_text, tag_model, api_key)
                if extract_tags_enabled
                else []
            )
            mark_job_progress(job_id, "刷新 Semantic Scholar 元数据")
            semantic_result = refresh_conversation_semantic_result(session, conversation_id, final_title)
            response_payload = {
                "conversation_id": conversation_id,
                "title": final_title,
                "messages": [
                    {
                        "id": None,
                        "role": "bot",
                        "message_kind": "bot_reply",
                        "section_category": response_section_category,
                        "visible_to_user": True,
                        "content": response_text,
                        "display_content": response_text,
                        **({"client_payload": response_client_payload} if response_client_payload else {}),
                        **({"translation_status": response_status} if response_status else {}),
                    }
                ],
                "translation_status": response_status,
                "display_reply": response_text,
                "pdf_url": pdf_attachment.url,
                "figures": serialize_figures(figures),
                "tables": serialize_tables(tables),
                "tags": serialize_tags(tags),
            }
            response_payload.update(serialize_semantic_result(semantic_result))

        return response_payload
    finally:
        if upload_path.exists():
            upload_path.unlink()


async def run_continue_job(job_id: str, payload: dict, job_type: str) -> dict:
    conversation_id = str(payload.get("conversation_id", "")).strip()
    new_user_message = str(payload.get("new_user_message", "")).strip()
    poe_model = str(payload.get("poe_model", settings.poe_model))
    api_key = str(payload.get("api_key", "")).strip()
    save_to_record = as_bool(payload.get("save_to_record", True))

    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required.")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key is required.")
    if not new_user_message:
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    mark_job_progress(job_id, "加载会话上下文")
    with Session(engine) as session:
        response = await continue_conversation(
            conversation_id=conversation_id,
            new_user_message=new_user_message,
            poe_model=poe_model,
            api_key=api_key,
            session=session,
            save_to_record=save_to_record,
            is_continue_command=(job_type == "continue"),
            progress_callback=lambda progress: mark_job_progress(job_id, progress),
        )
    mark_job_progress(job_id, "整理返回结果")
    return {"conversation_id": conversation_id, **response}


def queue_upload_from_file(
    *,
    filename: str,
    poe_model: str,
    title_model: str,
    tag_model: str,
    extract_tags: bool,
    api_key: str,
    file_bytes: bytes,
) -> dict:
    JOB_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    staged_path = JOB_UPLOAD_DIR / f"{uuid.uuid4().hex}.pdf"
    staged_path.write_bytes(file_bytes)

    return enqueue_async_job(
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
def get_job_status_payload(session: Session, job_id: str) -> dict:
    job = crud.get_async_job(session, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    payload = serialize_async_job(job)
    if job.conversation_id:
        conversation = crud.get_conversation(session, job.conversation_id)
        if conversation and conversation.title:
            payload["conversation_title"] = conversation.title
    return payload
