from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from .config import settings
from .database import engine
from .models import AsyncJob, Conversation

router = APIRouter(prefix="/tasks", tags=["tasks"])

JOB_QUEUE: asyncio.Queue[str] = asyncio.Queue()
JOB_WORKERS: list[asyncio.Task] = []
SESSION_ENQUEUE_LOCKS: dict[str, asyncio.Lock] = {}
SESSION_ENQUEUE_LOCKS_GUARD = asyncio.Lock()


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _safe_json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


class TaskStatusResponse(BaseModel):
    task_id: str
    task_type: str
    status: str
    progress: str = ""
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime
    result: Optional[dict] = None
    error_message: Optional[str] = None


@dataclass(frozen=True)
class TaskDefinition:
    payload_model: type[BaseModel]
    handler: Callable[[str, BaseModel], Awaitable[dict]]


TASK_DEFINITIONS: dict[str, TaskDefinition] = {}


def register_task_definition(task_type: str, payload_model: type[BaseModel], handler: Callable[[str, BaseModel], Awaitable[dict]]) -> None:
    TASK_DEFINITIONS[task_type] = TaskDefinition(payload_model=payload_model, handler=handler)


def create_task_record(session: Session, task_id: str, task_type: str, payload: dict, conversation_id: str | None = None) -> AsyncJob:
    now = datetime.now(timezone.utc)
    job = AsyncJob(
        id=task_id,
        job_type=task_type,
        status="queued",
        progress="排队中",
        payload_json=_json_dumps(payload),
        conversation_id=conversation_id,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def update_task_record(
    task_id: str,
    *,
    status: str | None = None,
    progress: str | None = None,
    result: dict | None = None,
    error_message: str | None = None,
    conversation_id: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    with Session(engine) as session:
        job = session.get(AsyncJob, task_id)
        if not job:
            return
        now = datetime.now(timezone.utc)
        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = progress
        if result is not None:
            job.result_json = _json_dumps(result)
        if error_message is not None:
            job.error_message = error_message
        if conversation_id is not None:
            job.conversation_id = conversation_id
        if started and job.started_at is None:
            job.started_at = now
        if finished:
            job.finished_at = now
        job.updated_at = now
        session.add(job)
        session.commit()


def mark_task_progress(task_id: str, progress: str) -> None:
    update_task_record(task_id, progress=progress)


def enqueue_task(task_type: str, payload_model: BaseModel, conversation_id: str | None = None) -> dict:
    if task_type not in TASK_DEFINITIONS:
        raise RuntimeError(f"Unsupported task type: {task_type}")
    task_id = uuid.uuid4().hex
    with Session(engine) as session:
        create_task_record(
            session=session,
            task_id=task_id,
            task_type=task_type,
            payload=payload_model.model_dump(mode="json"),
            conversation_id=conversation_id,
        )
    JOB_QUEUE.put_nowait(task_id)
    return {"task_id": task_id, "status": "queued"}


async def get_session_enqueue_lock(conversation_id: str) -> asyncio.Lock:
    async with SESSION_ENQUEUE_LOCKS_GUARD:
        lock = SESSION_ENQUEUE_LOCKS.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            SESSION_ENQUEUE_LOCKS[conversation_id] = lock
        return lock


def recover_pending_tasks() -> None:
    pending_ids: list[str] = []
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        jobs = session.exec(
            select(AsyncJob)
            .where(AsyncJob.status.in_(["queued", "running"]))
            .order_by(AsyncJob.created_at.asc())
        ).all()
        for job in jobs:
            pending_ids.append(job.id)
            if job.status == "running":
                job.status = "queued"
                job.progress = "服务已重启，任务重新排队中"
                job.started_at = None
                job.updated_at = now
                session.add(job)
        session.commit()
    for job_id in pending_ids:
        JOB_QUEUE.put_nowait(job_id)


def start_task_workers() -> None:
    if JOB_WORKERS:
        return
    worker_count = max(1, settings.async_job_workers)
    for index in range(worker_count):
        JOB_WORKERS.append(asyncio.create_task(task_worker_loop(index + 1), name=f"task-worker-{index + 1}"))


async def stop_task_workers() -> None:
    for worker in JOB_WORKERS:
        worker.cancel()
    if JOB_WORKERS:
        await asyncio.gather(*JOB_WORKERS, return_exceptions=True)
    JOB_WORKERS.clear()


async def task_worker_loop(worker_index: int) -> None:
    while True:
        task_id = await JOB_QUEUE.get()
        try:
            await process_task(task_id, worker_index)
        finally:
            JOB_QUEUE.task_done()


async def process_task(task_id: str, worker_index: int) -> None:
    with Session(engine) as session:
        job = session.get(AsyncJob, task_id)
        if not job or job.status != "queued":
            return
        definition = TASK_DEFINITIONS.get(job.job_type)
        if definition is None:
            raise RuntimeError(f"Missing task definition: {job.job_type}")
        payload_dict = _safe_json_loads(job.payload_json, {})
        payload = definition.payload_model.model_validate(payload_dict)
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
        result = await definition.handler(task_id, payload)
        update_task_record(
            task_id,
            status="succeeded",
            progress="任务已完成",
            result=result,
            error_message="",
            conversation_id=result.get("conversation_id") if isinstance(result, dict) else None,
            finished=True,
        )
    except HTTPException as exc:
        detail = exc.detail
        error_message = detail if isinstance(detail, str) else _json_dumps(detail)
        update_task_record(task_id, status="failed", progress="任务失败", error_message=error_message, finished=True)
    except Exception as exc:
        update_task_record(task_id, status="failed", progress="任务失败", error_message=str(exc), finished=True)


def serialize_task(job: AsyncJob, conversation_title: str | None = None) -> dict:
    payload = TaskStatusResponse(
        task_id=job.id,
        task_type=job.job_type,
        status=job.status,
        progress=job.progress or "",
        conversation_id=job.conversation_id,
        conversation_title=conversation_title,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        updated_at=job.updated_at,
        result=_safe_json_loads(job.result_json, {}) if job.status == "succeeded" else None,
        error_message=(job.error_message or "任务执行失败。") if job.status == "failed" else None,
    )
    return payload.model_dump()


def get_task_status_payload(session: Session, task_id: str) -> dict:
    job = session.get(AsyncJob, task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Task not found.")
    conversation_title = None
    if job.conversation_id:
        conversation = session.get(Conversation, job.conversation_id)
        if conversation:
            conversation_title = conversation.title
    return serialize_task(job, conversation_title)


def get_active_task(session: Session, conversation_id: str, task_types: list[str]) -> AsyncJob | None:
    statement = (
        select(AsyncJob)
        .where(
            AsyncJob.conversation_id == conversation_id,
            AsyncJob.job_type.in_(task_types),
            AsyncJob.status.in_(["queued", "running"]),
        )
        .order_by(AsyncJob.created_at.asc())
    )
    return session.exec(statement).first()


@router.get("/{task_id}")
async def get_task_status(task_id: str):
    with Session(engine) as session:
        return get_task_status_payload(session, task_id)
