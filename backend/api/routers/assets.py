from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException
from sqlmodel import Session

from ...core.dependencies import check_read_only, get_db_session
from ...persistence import crud
from ...persistence.models import PaperFigure, PaperTable
from ...services.annotations import (
    build_asset_response,
    download_pdf_bytes,
    extract_and_store_figures,
    extract_and_store_tables,
)
from ...services.serializers import serialize_figures, serialize_tables

router = APIRouter()


@router.get("/assets/figures/{figure_id}")
async def get_figure_asset(figure_id: int, session: Session = Depends(get_db_session)):
    figure = session.get(PaperFigure, figure_id)
    return build_asset_response(figure)


@router.get("/assets/tables/{table_id}")
async def get_table_asset(table_id: int, session: Session = Depends(get_db_session)):
    table = session.get(PaperTable, table_id)
    return build_asset_response(table)


@router.post("/conversation/{conversation_id}/reprocess_assets")
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
        file_bytes = download_pdf_bytes(file_record.poe_url)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    figures = crud.get_figures(session, conversation_id)
    tables = crud.get_tables(session, conversation_id)

    if figure_caption_direction is not None:
        figures = extract_and_store_figures(session, conversation_id, file_bytes, figure_caption_direction)
    if table_caption_direction is not None:
        tables = extract_and_store_tables(session, conversation_id, file_bytes, table_caption_direction)

    return {
        "figure_caption_direction": figure_caption_direction,
        "table_caption_direction": table_caption_direction,
        "figures": serialize_figures(figures),
        "tables": serialize_tables(tables),
    }
