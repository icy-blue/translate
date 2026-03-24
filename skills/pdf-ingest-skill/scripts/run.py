#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlmodel import Session

from backend import crud
from backend.database import engine


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_error(code: str, message: str, errors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message},
        "errors": errors or [],
    }


def _load_file_bytes(payload: dict[str, Any]) -> tuple[bytes, str]:
    file_path = str(payload.get("file_path", "")).strip()
    b64 = payload.get("file_bytes_base64")

    if file_path:
        content = Path(file_path).read_bytes()
        filename = str(payload.get("filename", "")).strip() or Path(file_path).name
        return content, filename

    if isinstance(b64, str) and b64.strip():
        content = base64.b64decode(b64.strip())
        filename = str(payload.get("filename", "")).strip() or "upload.pdf"
        return content, filename

    raise ValueError("file_path or file_bytes_base64 is required.")


def main() -> int:
    parser = argparse.ArgumentParser(description="pdf-ingest-skill runner")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    warnings: list[dict[str, Any]] = []
    try:
        payload = _read_json(args.input_json)
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_input", f"Failed to parse input json: {exc}"))
        return 1

    try:
        content, filename = _load_file_bytes(payload)
    except Exception as exc:
        _write_json(args.output_json, _result_error("read_failed", str(exc)))
        return 1

    fingerprint = hashlib.sha256(content).hexdigest()
    is_existing = False
    existing_conversation_id = None

    if bool(payload.get("check_existing", True)):
        try:
            with Session(engine) as session:
                existing = crud.find_existing_file(session, fingerprint)
                if existing:
                    is_existing = True
                    existing_conversation_id = existing.conversation_id
        except Exception as exc:
            warnings.append(
                {
                    "skill": "pdf-ingest-skill",
                    "type": "warning",
                    "message": f"DB duplicate check failed: {exc}",
                    "retryable": True,
                }
            )

    _write_json(
        args.output_json,
        {
            "ok": True,
            "filename": filename,
            "file_size": len(content),
            "file_bytes_base64": base64.b64encode(content).decode("utf-8"),
            "fingerprint": fingerprint,
            "is_existing": is_existing,
            "existing_conversation_id": existing_conversation_id,
            "errors": warnings,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
