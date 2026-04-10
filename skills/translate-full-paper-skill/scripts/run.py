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

from backend.domain.message_payloads import (
    build_initial_translation_prompt,
    build_translation_status_payload,
    build_unit_translation_prompt,
    normalize_raw_translation_result_payload,
    normalize_translation_glossary_payload,
    normalize_translation_plan_payload,
    normalize_translation_status_payload,
    parse_translation_glossary_response,
    parse_raw_translation_status_block,
    parse_translation_plan_response,
    preprocess_bot_reply_for_storage,
)
from backend.platform.config import settings
from backend.platform.gateways.poe import get_bot_response


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


def _build_user_message(content: str, *, message_kind: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": content,
        "message_kind": message_kind,
        "visible_to_user": False,
    }


def _build_bot_message(
    response_text: str,
    *,
    translation_plan: dict[str, Any],
    translation_status: dict[str, Any],
    translation_glossary: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    prepared = preprocess_bot_reply_for_storage(
        response_text,
        {
            "translation_plan": translation_plan,
            "translation_status": translation_status,
            "translation_glossary": translation_glossary,
        },
    )
    content = str(prepared["content"])
    return (
        {
            "role": "bot",
            "content": content,
            "message_kind": "bot_reply",
            "visible_to_user": True,
            "section_category": None,
            "client_payload": prepared["client_payload"],
        },
        content,
    )


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = str(payload.get("api_key", "")).strip()
    poe_model = str(payload.get("poe_model", "")).strip() or settings.poe_model
    planner_prompt = build_initial_translation_prompt(str(payload.get("initial_prompt", "")).strip() or settings.initial_prompt)
    continue_count = int(payload.get("continue_count", 0) or 0)
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
    result_messages: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    try:
        planner_reply = await get_bot_response(
            [fp.ProtocolMessage(role="user", content=planner_prompt, attachments=[attachment])],
            poe_model,
            api_key,
        )
    except Exception as exc:
        return _result_error("planner_failed", f"Planner failed: {exc}", result_messages)

    translation_plan = normalize_translation_plan_payload(parse_translation_plan_response(planner_reply))
    translation_glossary = normalize_translation_glossary_payload(parse_translation_glossary_response(planner_reply))
    if translation_glossary is None:
        translation_glossary = normalize_translation_glossary_payload({"status": "confirmed", "entries": []})
    elif translation_glossary["entries"]:
        translation_glossary = normalize_translation_glossary_payload(
            {
                "status": "confirmed",
                "entries": translation_glossary["entries"],
            }
        )
    if translation_plan is None:
        translation_plan = normalize_translation_plan_payload(
            {"status": "unsupported", "units": [], "appendix_units": [], "reason": "planner_parse_failed"}
        )
    if translation_plan is None:
        return _result_error("planner_failed", "Failed to normalize planner output.", result_messages)
    if translation_plan["status"] == "unsupported" or not translation_plan["units"]:
        latest_status = build_translation_status_payload(translation_plan, completed_unit_ids=[])
        result_messages.append(_build_user_message(planner_prompt, message_kind="system_prompt"))
        bot_message, cleaned_content = _build_bot_message(
            "",
            translation_plan=translation_plan,
            translation_status=latest_status,
            translation_glossary=translation_glossary,
        )
        result_messages.append(bot_message)
        return {
            "ok": True,
            "messages": result_messages,
            "first_bot_message": cleaned_content,
            "continue_count_used": 0,
            "translation_plan": translation_plan,
            "translation_status": latest_status,
            "translation_glossary": translation_glossary,
            "errors": errors,
        }

    current_unit_id = translation_plan["units"][0]
    unit_prompt = build_unit_translation_prompt(
        settings.continue_prompt,
        active_units=translation_plan["units"],
        current_unit_id=current_unit_id,
        translation_glossary=translation_glossary,
    )
    result_messages.append(_build_user_message(unit_prompt, message_kind="system_prompt"))
    try:
        first_reply = await get_bot_response(
            [fp.ProtocolMessage(role="user", content=unit_prompt, attachments=[attachment])],
            poe_model,
            api_key,
        )
    except Exception as exc:
        return _result_error("initial_translate_failed", f"Initial translation failed: {exc}", result_messages)

    first_reply = first_reply or ""
    first_raw_result = normalize_raw_translation_result_payload(parse_raw_translation_status_block(first_reply))
    if first_raw_result is None:
        first_raw_result = {
            "current_unit_id": current_unit_id,
            "state": "UNSUPPORTED",
            "reason": "translator_status_missing",
        }
    completed_unit_ids = [current_unit_id] if first_raw_result and first_raw_result["state"] == "OK" else []
    latest_status = build_translation_status_payload(
        translation_plan,
        completed_unit_ids=completed_unit_ids,
        current_unit_id=current_unit_id,
        attempted_scope="body",
        raw_translation_result=first_raw_result,
    )
    first_bot_message, first_bot_content = _build_bot_message(
        first_reply,
        translation_plan=translation_plan,
        translation_status=latest_status,
        translation_glossary=translation_glossary,
    )
    result_messages.append(first_bot_message)
    continue_count_used = 0
    for _ in range(max(0, continue_count)):
        if latest_status is None:
            errors.append(
                {
                    "skill": "translate-full-paper-skill",
                    "type": "warning",
                    "message": "Continue loop interrupted: latest reply has no canonical unit translation_status.",
                    "retryable": False,
                }
            )
            break
        latest_status = normalize_translation_status_payload(latest_status)
        if latest_status is None or latest_status["state"] in {"UNSUPPORTED", "ALL_DONE"}:
            break
        if latest_status["active_scope"] == "appendix":
            active_units = translation_plan["appendix_units"]
        else:
            active_units = translation_plan["units"]
        next_unit_id = str(latest_status.get("next_unit_id", "")).strip()
        if not next_unit_id:
            break
        continue_prompt = build_unit_translation_prompt(
            settings.continue_prompt,
            active_units=active_units,
            current_unit_id=next_unit_id,
            translation_glossary=translation_glossary,
        )
        result_messages.append(_build_user_message(continue_prompt, message_kind="continue_command"))
        try:
            reply = await get_bot_response(
                [fp.ProtocolMessage(role="user", content=continue_prompt, attachments=[attachment])],
                poe_model,
                api_key,
            )
            reply = reply or ""
            raw_result = normalize_raw_translation_result_payload(parse_raw_translation_status_block(reply))
            if raw_result is None:
                raw_result = {
                    "current_unit_id": next_unit_id,
                    "state": "UNSUPPORTED",
                    "reason": "translator_status_missing",
                }
            if raw_result and raw_result["state"] == "OK" and next_unit_id not in completed_unit_ids:
                completed_unit_ids.append(next_unit_id)
            latest_status = build_translation_status_payload(
                translation_plan,
                completed_unit_ids=completed_unit_ids,
                current_unit_id=next_unit_id,
                attempted_scope=latest_status["active_scope"] or "body",
                raw_translation_result=raw_result,
            )
            bot_message, _ = _build_bot_message(
                reply,
                translation_plan=translation_plan,
                translation_status=latest_status,
                translation_glossary=translation_glossary,
            )
            result_messages.append(bot_message)
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
        "first_bot_message": first_bot_content,
        "continue_count_used": continue_count_used,
        "translation_plan": translation_plan,
        "translation_status": latest_status,
        "translation_glossary": translation_glossary,
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
