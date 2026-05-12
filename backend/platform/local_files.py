from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_FILES_DIR = PROJECT_ROOT / "files"
LOCAL_FILES_URL_PREFIX = "/files"


def ensure_local_files_dir() -> Path:
    LOCAL_FILES_DIR.mkdir(parents=True, exist_ok=True)
    return LOCAL_FILES_DIR


def build_pdf_relative_path(conversation_id: str, file_id: str) -> Path:
    return Path(conversation_id) / f"{file_id}.pdf"


def build_pdf_url(conversation_id: str, file_id: str) -> str:
    return f"{LOCAL_FILES_URL_PREFIX}/{conversation_id}/{file_id}.pdf"


def local_file_url_to_path(url: str) -> Path | None:
    if not url.startswith(f"{LOCAL_FILES_URL_PREFIX}/"):
        return None
    relative = unquote(url[len(LOCAL_FILES_URL_PREFIX) + 1 :])
    path = (LOCAL_FILES_DIR / relative).resolve()
    root = LOCAL_FILES_DIR.resolve()
    if root != path and root not in path.parents:
        raise ValueError("Local file URL escapes the files directory.")
    return path


def write_pdf_file(conversation_id: str, file_id: str, file_bytes: bytes, *, overwrite: bool = True) -> str:
    target = ensure_local_files_dir() / build_pdf_relative_path(conversation_id, file_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not overwrite:
        return build_pdf_url(conversation_id, file_id)
    target.write_bytes(file_bytes)
    return build_pdf_url(conversation_id, file_id)
