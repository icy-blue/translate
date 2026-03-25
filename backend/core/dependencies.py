from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Form, Header
from sqlmodel import Session

from .config import settings
from .database import engine

def get_db_session():
    """FastAPI dependency to get a DB session."""
    with Session(engine) as session:
        yield session

def check_read_only():
    """FastAPI dependency to check if the system is in read-only mode."""
    if settings.read_only:
        raise HTTPException(status_code=403, detail="System is in read-only mode.")

def get_api_key(api_key: str = Form(...)) -> str:
    """FastAPI dependency to get and validate the Poe API key."""
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key is required.")
    return api_key


def get_agent_ingest_token(x_agent_token: Optional[str] = Header(default=None)) -> str:
    """Validate agent ingestion token via x-agent-token header."""
    expected = (settings.agent_ingest_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="Agent ingestion is not configured.")
    if not x_agent_token or x_agent_token != expected:
        raise HTTPException(status_code=401, detail="Invalid agent token.")
    return x_agent_token
