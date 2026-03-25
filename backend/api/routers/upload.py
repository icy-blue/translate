from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ...core.config import settings
from ...core.dependencies import check_read_only, get_api_key
from ...services.async_jobs import queue_upload_from_file

router = APIRouter()


@router.post("/upload")
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

    return queue_upload_from_file(
        filename=filename,
        poe_model=poe_model,
        title_model=title_model,
        tag_model=tag_model,
        extract_tags=extract_tags,
        api_key=api_key,
        file_bytes=file_bytes,
    )
