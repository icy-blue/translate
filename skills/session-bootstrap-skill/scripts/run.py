#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pypdf import PdfReader, PdfWriter

from backend.platform.gateways.poe import extract_title_from_pdf, upload_file


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}, "errors": []}


def _build_first_page_pdf_bytes(content: bytes) -> bytes | None:
    try:
        reader = PdfReader(io.BytesIO(content))
        if len(reader.pages) == 0:
            return None
        writer = PdfWriter()
        writer.add_page(reader.pages[0])
        output = io.BytesIO()
        writer.write(output)
        output.seek(0)
        return output.read()
    except Exception:
        return None


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    filename = str(payload.get("filename", "")).strip() or "upload.pdf"
    api_key = str(payload.get("api_key", "")).strip()
    title_model = str(payload.get("title_model", "")).strip() or "GPT-5.2-Instant"
    raw_b64 = payload.get("file_bytes_base64")

    if not api_key:
        return _result_error("invalid_input", "api_key is required.")
    if not isinstance(raw_b64, str) or not raw_b64.strip():
        return _result_error("invalid_input", "file_bytes_base64 is required.")

    try:
        content = base64.b64decode(raw_b64.strip())
    except Exception as exc:
        return _result_error("invalid_input", f"Invalid file_bytes_base64: {exc}")

    errors: list[dict[str, Any]] = []

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
            tmp.write(content)
            tmp.flush()
            with open(tmp.name, "rb") as fp:
                attachment = await upload_file(fp, api_key, filename)
    except Exception as exc:
        return _result_error("poe_upload_failed", f"Failed to upload original PDF: {exc}")

    title_attachment = attachment
    first_page_pdf = _build_first_page_pdf_bytes(content)
    if first_page_pdf:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp_first:
                tmp_first.write(first_page_pdf)
                tmp_first.flush()
                with open(tmp_first.name, "rb") as fp:
                    title_attachment = await upload_file(fp, api_key, f"first_page_{filename}")
        except Exception as exc:
            errors.append(
                {
                    "skill": "session-bootstrap-skill",
                    "type": "warning",
                    "message": f"First-page upload failed, fallback to original file: {exc}",
                    "retryable": True,
                }
            )

    title = filename
    try:
        extracted = await extract_title_from_pdf(title_attachment, api_key, title_model)
        if extracted and extracted.strip():
            title = extracted.strip()
    except Exception as exc:
        errors.append(
            {
                "skill": "session-bootstrap-skill",
                "type": "warning",
                "message": f"Title extraction failed, fallback to filename: {exc}",
                "retryable": True,
            }
        )

    conversation_id = str(payload.get("conversation_id", "")).strip() or uuid.uuid4().hex[:12]
    file_id = uuid.uuid4().hex

    return {
        "ok": True,
        "conversation_id": conversation_id,
        "file_id": file_id,
        "poe_attachment": {
            "url": attachment.url,
            "content_type": attachment.content_type,
            "name": attachment.name,
        },
        "title": title,
        "file_record": {
            "filename": filename,
            "poe_url": attachment.url,
            "content_type": attachment.content_type,
            "poe_name": attachment.name,
        },
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="session-bootstrap-skill runner")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    try:
        payload = _read_json(args.input_json)
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_input", f"Failed to parse input json: {exc}"))
        return 1

    result = asyncio.run(_run(payload))
    _write_json(args.output_json, result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
