#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.platform.gateways.semantic_scholar import build_result_payload, fetch_semantic_scholar_match


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}, "meta": None, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser(description="refresh-metadata-skill runner")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    try:
        payload = _read_json(args.input_json)
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_input", f"Failed to parse input json: {exc}"))
        return 1

    title = str(payload.get("title", "")).strip()
    conversation_id = str(payload.get("conversation_id", "")).strip() or uuid.uuid4().hex[:12]
    api_key = str(payload.get("semantic_api_key", "")).strip() or None

    if not title:
        _write_json(args.output_json, _result_error("invalid_input", "title is required."))
        return 1

    try:
        response_payload = fetch_semantic_scholar_match(title=title, api_key=api_key)
        normalized = build_result_payload(conversation_id, response_payload)
    except Exception as exc:
        _write_json(args.output_json, _result_error("metadata_failed", str(exc)))
        return 1

    _write_json(args.output_json, {"ok": True, "meta": normalized, "errors": []})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
