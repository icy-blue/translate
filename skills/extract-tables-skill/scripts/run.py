#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain.pdf_figures import extract_pdf_tables


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}, "tables": [], "errors": []}


def _load_bytes(payload: dict[str, Any]) -> bytes:
    file_path = str(payload.get("file_path", "")).strip()
    raw_b64 = payload.get("file_bytes_base64")
    if file_path:
        return Path(file_path).read_bytes()
    if isinstance(raw_b64, str) and raw_b64.strip():
        return base64.b64decode(raw_b64.strip())
    raise ValueError("file_path or file_bytes_base64 is required.")


def main() -> int:
    parser = argparse.ArgumentParser(description="extract-tables-skill runner")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    try:
        payload = _read_json(args.input_json)
        content = _load_bytes(payload)
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_input", str(exc)))
        return 1

    preferred_direction = str(payload.get("preferred_direction", "")).strip() or None
    try:
        tables = extract_pdf_tables(content, preferred_direction=preferred_direction)
    except Exception as exc:
        _write_json(args.output_json, _result_error("extract_failed", str(exc)))
        return 1

    encoded: list[dict[str, Any]] = []
    for item in tables:
        row = dict(item)
        image_data = row.pop("image_data", None)
        row["image_data_base64"] = base64.b64encode(image_data).decode("utf-8") if image_data else None
        encoded.append(row)

    _write_json(args.output_json, {"ok": True, "tables": encoded, "errors": []})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
