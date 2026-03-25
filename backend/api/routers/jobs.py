from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session

from ...core.dependencies import get_db_session
from ...services.async_jobs import get_job_status_payload

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str, session: Session = Depends(get_db_session)):
    return get_job_status_payload(session, job_id)
