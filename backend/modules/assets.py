from __future__ import annotations

import urllib.error
import urllib.request
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from ..app.dependencies import check_read_only, get_db_session
from ..domain.pdf_figures import extract_pdf_figures, extract_pdf_tables
from ..platform.models import PaperFigure, PaperTable
from .conversations import get_figures, get_file_record, get_tables, serialize_figures, serialize_tables

router = APIRouter(tags=["assets"])


class ReprocessAssetsResponse(BaseModel):
    figure_caption_direction: Optional[str] = None
    table_caption_direction: Optional[str] = None
    figures: list = Field(default_factory=list)
    tables: list = Field(default_factory=list)


def get_figure(session: Session, figure_id: int) -> Optional[PaperFigure]:
    return session.get(PaperFigure, figure_id)


def get_table(session: Session, table_id: int) -> Optional[PaperTable]:
    return session.get(PaperTable, table_id)


def replace_figures(session: Session, conversation_id: str, figures: list[dict]) -> None:
    existing_figures = get_figures(session, conversation_id)
    for figure in existing_figures:
        session.delete(figure)
    for figure in figures:
        session.add(
            PaperFigure(
                conversation_id=conversation_id,
                page_number=figure["page_number"],
                figure_index=figure["figure_index"],
                figure_label=figure.get("figure_label"),
                caption=figure["caption"],
                image_mime_type=figure.get("image_mime_type"),
                image_data=figure.get("image_data"),
                image_width=figure["image_width"],
                image_height=figure["image_height"],
            )
        )
    session.commit()


def replace_tables(session: Session, conversation_id: str, tables: list[dict]) -> None:
    existing_tables = get_tables(session, conversation_id)
    for table in existing_tables:
        session.delete(table)
    for table in tables:
        session.add(
            PaperTable(
                conversation_id=conversation_id,
                page_number=table["page_number"],
                table_index=table["table_index"],
                table_label=table.get("table_label"),
                caption=table["caption"],
                image_mime_type=table.get("image_mime_type"),
                image_data=table.get("image_data"),
                image_width=table["image_width"],
                image_height=table["image_height"],
            )
        )
    session.commit()


def extract_and_store_figures(session: Session, conversation_id: str, file_bytes: bytes, preferred_direction: Optional[str] = None):
    try:
        extracted_figures = extract_pdf_figures(file_bytes, preferred_direction=preferred_direction)
        replace_figures(session, conversation_id, extracted_figures)
        return get_figures(session, conversation_id)
    except Exception as exc:
        print(f"Error extracting figures for conversation {conversation_id}: {exc}")
        session.rollback()
        return get_figures(session, conversation_id)


def extract_and_store_tables(session: Session, conversation_id: str, file_bytes: bytes, preferred_direction: Optional[str] = None):
    try:
        extracted_tables = extract_pdf_tables(file_bytes, preferred_direction=preferred_direction)
        replace_tables(session, conversation_id, extracted_tables)
        return get_tables(session, conversation_id)
    except Exception as exc:
        print(f"Error extracting tables for conversation {conversation_id}: {exc}")
        session.rollback()
        return get_tables(session, conversation_id)


def build_asset_response(asset):
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found.")
    if asset.image_data is not None:
        return Response(content=bytes(asset.image_data), media_type=asset.image_mime_type or "image/webp")
    raise HTTPException(status_code=404, detail="Asset data not found.")


def download_pdf_bytes(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "translate-reprocess/1.0", "Accept": "application/pdf,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError(f"Failed to download PDF from {url}: {exc}") from exc


def reprocess_assets(
    session: Session,
    conversation_id: str,
    *,
    figure_caption_direction: Optional[str] = None,
    table_caption_direction: Optional[str] = None,
) -> dict:
    file_record = get_file_record(session, conversation_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")
    try:
        file_bytes = download_pdf_bytes(file_record.poe_url)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    figures = get_figures(session, conversation_id)
    tables = get_tables(session, conversation_id)
    if figure_caption_direction is not None:
        figures = extract_and_store_figures(session, conversation_id, file_bytes, figure_caption_direction)
    if table_caption_direction is not None:
        tables = extract_and_store_tables(session, conversation_id, file_bytes, table_caption_direction)
    return {
        "figure_caption_direction": figure_caption_direction,
        "table_caption_direction": table_caption_direction,
        "figures": [figure.model_dump() for figure in serialize_figures(figures)],
        "tables": [table.model_dump() for table in serialize_tables(tables)],
    }


@router.get("/assets/figures/{figure_id}")
async def get_figure_asset(figure_id: int, session: Session = Depends(get_db_session)):
    return build_asset_response(get_figure(session, figure_id))


@router.get("/assets/tables/{table_id}")
async def get_table_asset(table_id: int, session: Session = Depends(get_db_session)):
    return build_asset_response(get_table(session, table_id))


@router.post("/assets/{conversation_id}/reprocess")
async def reprocess_conversation_assets(
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
    for field_name, value in {"figure_caption_direction": figure_caption_direction, "table_caption_direction": table_caption_direction}.items():
        if value is not None and value not in {"above", "below"}:
            raise HTTPException(status_code=400, detail=f"{field_name} must be 'above' or 'below'.")
    if figure_caption_direction is None and table_caption_direction is None:
        raise HTTPException(status_code=400, detail="At least one caption direction must be provided.")
    return reprocess_assets(
        session,
        conversation_id,
        figure_caption_direction=figure_caption_direction,
        table_caption_direction=table_caption_direction,
    )
