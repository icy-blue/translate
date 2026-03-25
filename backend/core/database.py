from sqlmodel import create_engine

from .config import settings

DATABASE_URL = settings.database_url
engine = create_engine(DATABASE_URL, echo=False)
