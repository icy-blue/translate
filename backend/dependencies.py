from fastapi import Depends, HTTPException, Form
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
