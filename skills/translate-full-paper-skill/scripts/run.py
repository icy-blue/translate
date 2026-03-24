#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import fastapi_poe as fp

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.poe_utils import get_bot_response


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_error(code: str, message: str, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {"code": code, "message": message},
        "messages": messages or [],
        "errors": [],
    }


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = str(payload.get("api_key", "")).strip()
    poe_model = str(payload.get("poe_model", "")).strip() or settings.poe_model
    initial_prompt = str(payload.get("initial_prompt", "")).strip() or settings.initial_prompt
    continue_count = int(payload.get("continue_count", 0) or 0)
    continue_message = str(payload.get("continue_message", "")).strip() or "继续"
    attachment_payload = payload.get("poe_attachment") if isinstance(payload.get("poe_attachment"), dict) else None

    if not api_key:
        return _result_error("invalid_input", "api_key is required.")
    if not attachment_payload:
        return _result_error("invalid_input", "poe_attachment is required.")

    attachment_url = str(attachment_payload.get("url", "")).strip()
    attachment_type = str(attachment_payload.get("content_type", "")).strip() or "application/pdf"
    attachment_name = str(attachment_payload.get("name", "")).strip() or "upload.pdf"
    if not attachment_url:
        return _result_error("invalid_input", "poe_attachment.url is required.")

    attachment = fp.Attachment(url=attachment_url, content_type=attachment_type, name=attachment_name)
    poe_messages: list[fp.ProtocolMessage] = [
        fp.ProtocolMessage(role="user", content=initial_prompt, attachments=[attachment])
    ]
    result_messages: list[dict[str, Any]] = [{"role": "user", "content": initial_prompt}]
    errors: list[dict[str, Any]] = []

    try:
        first_reply = await get_bot_response(poe_messages, poe_model, api_key)
    except Exception as exc:
        return _result_error("initial_translate_failed", f"Initial translation failed: {exc}", result_messages)

    first_reply = first_reply or ""
    poe_messages.append(fp.ProtocolMessage(role="bot", content=first_reply))
    result_messages.append({"role": "bot", "content": first_reply})

    continue_count_used = 0
    for _ in range(max(0, continue_count)):
        poe_messages.append(fp.ProtocolMessage(role="user", content=continue_message))
        result_messages.append({"role": "user", "content": continue_message})
        try:
            reply = await get_bot_response(poe_messages, poe_model, api_key)
            reply = reply or ""
            poe_messages.append(fp.ProtocolMessage(role="bot", content=reply))
            result_messages.append({"role": "bot", "content": reply})
            continue_count_used += 1
        except Exception as exc:
            errors.append(
                {
                    "skill": "translate-full-paper-skill",
                    "type": "warning",
                    "message": f"Continue loop interrupted: {exc}",
                    "retryable": True,
                }
            )
            break

    return {
        "ok": True,
        "messages": result_messages,
        "first_bot_message": first_reply,
        "continue_count_used": continue_count_used,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="translate-full-paper-skill runner")
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
