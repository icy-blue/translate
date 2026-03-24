#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}, "bundle": None, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser(description="compose-pipeline-bundle-skill runner")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    try:
        payload = _read_json(args.input_json)
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_input", f"Failed to parse input json: {exc}"))
        return 1

    title = str(payload.get("title", "")).strip()
    file_record = payload.get("file_record") if isinstance(payload.get("file_record"), dict) else None
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else None

    if not title:
        _write_json(args.output_json, _result_error("invalid_bundle", "title is required."))
        return 1
    if not file_record:
        _write_json(args.output_json, _result_error("invalid_bundle", "file_record is required."))
        return 1
    if messages is None:
        _write_json(args.output_json, _result_error("invalid_bundle", "messages must be a list."))
        return 1

    bundle: dict[str, Any] = {
        "conversation_id": (str(payload.get("conversation_id", "")).strip() or None),
        "title": title,
        "file_record": file_record,
        "messages": messages,
        "figures": payload.get("figures") if isinstance(payload.get("figures"), list) else [],
        "tables": payload.get("tables") if isinstance(payload.get("tables"), list) else [],
        "tags": payload.get("tags") if isinstance(payload.get("tags"), list) else [],
        "meta": payload.get("meta") if isinstance(payload.get("meta"), dict) else None,
        "errors": payload.get("errors") if isinstance(payload.get("errors"), list) else [],
    }

    _write_json(args.output_json, {"ok": True, "bundle": bundle, "errors": []})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
