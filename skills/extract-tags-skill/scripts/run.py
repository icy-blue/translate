#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.paper_tags import extract_abstract_for_tagging
from backend.poe_utils import classify_paper_tags


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_error(code: str, message: str, abstract: str = "") -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message},
        "abstract": abstract,
        "tags": [],
        "errors": [],
    }


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(payload.get("enabled", True))
    title = str(payload.get("title", "")).strip()
    first_bot_message = str(payload.get("first_bot_message", ""))
    tag_model = str(payload.get("tag_model", "")).strip() or settings.poe_model
    api_key = str(payload.get("api_key", "")).strip()

    if not enabled:
        return {"ok": True, "abstract": "", "tags": [], "errors": []}

    if not api_key:
        return _result_error("invalid_input", "api_key is required when enabled.")

    abstract = extract_abstract_for_tagging(first_bot_message)
    if not title or not abstract:
        return {
            "ok": True,
            "abstract": abstract,
            "tags": [],
            "errors": [
                {
                    "skill": "extract-tags-skill",
                    "type": "warning",
                    "message": "title or abstract is empty, skip tag extraction.",
                    "retryable": False,
                }
            ],
        }

    try:
        tags = await classify_paper_tags(title, abstract, tag_model, api_key)
    except Exception as exc:
        return _result_error("classify_failed", str(exc), abstract)

    return {"ok": True, "abstract": abstract, "tags": tags, "errors": []}


def main() -> int:
    parser = argparse.ArgumentParser(description="extract-tags-skill runner")
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
