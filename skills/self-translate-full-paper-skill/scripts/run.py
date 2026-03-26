#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.domain.message_payloads import (
    normalize_translation_plan_payload,
    normalize_translation_status_payload,
    preprocess_bot_reply_for_storage,
)


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


def _require_string_path(value: Any, field_name: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required.")
    path = Path(raw)
    if not path.exists():
        raise ValueError(f"{field_name} does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"{field_name} must be a file: {path}")
    return path


def _normalize_user_message(message: dict[str, Any], index: int) -> dict[str, Any]:
    role = str(message.get("role", "")).strip().lower()
    if role != "user":
        raise ValueError(f"messages[{index}] must be a user message.")
    default_kind = "system_prompt" if index == 0 else "continue_command"
    message_kind = str(message.get("message_kind", "")).strip() or default_kind
    expected_kind = "system_prompt" if index == 0 else "continue_command"
    if message_kind != expected_kind:
        raise ValueError(f"messages[{index}] must use message_kind={expected_kind}.")
    return {
        "role": "user",
        "content": str(message.get("content", "")),
        "message_kind": message_kind,
        "visible_to_user": False,
    }


def _normalize_bot_message(
    message: dict[str, Any],
    index: int,
    expected_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str]:
    role = str(message.get("role", "")).strip().lower()
    if role != "bot":
        raise ValueError(f"messages[{index}] must be a bot message.")
    client_payload = message.get("client_payload")
    if not isinstance(client_payload, dict):
        raise ValueError(f"messages[{index}].client_payload is required for bot messages.")
    translation_plan = normalize_translation_plan_payload(client_payload.get("translation_plan"))
    translation_status = normalize_translation_status_payload(client_payload.get("translation_status"))
    if translation_plan is None:
        raise ValueError(f"messages[{index}] has invalid client_payload.translation_plan.")
    if translation_status is None:
        raise ValueError(f"messages[{index}] has invalid client_payload.translation_status.")
    if translation_plan != expected_plan:
        raise ValueError(f"messages[{index}] translation_plan does not match the top-level plan.")

    prepared = preprocess_bot_reply_for_storage(
        str(message.get("content", "")),
        {"translation_plan": translation_plan, "translation_status": translation_status},
    )
    cleaned_content = str(prepared["content"])
    normalized_payload = prepared["client_payload"]
    if not isinstance(normalized_payload, dict):
        raise ValueError(f"messages[{index}] produced an empty canonical client_payload.")
    return (
        {
            "role": "bot",
            "content": cleaned_content,
            "message_kind": "bot_reply",
            "visible_to_user": True,
            "section_category": None,
            "client_payload": normalized_payload,
        },
        translation_status,
        cleaned_content,
    )


def _normalize_messages(messages: Any, top_level_plan: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any] | None, str]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty list.")
    if len(messages) % 2 != 0:
        raise ValueError("messages must contain ordered user/bot pairs.")

    normalized: list[dict[str, Any]] = []
    latest_status: dict[str, Any] | None = None
    first_bot_message = ""

    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise ValueError(f"messages[{index}] must be an object.")
        if index % 2 == 0:
            normalized.append(_normalize_user_message(message, index))
        else:
            bot_message, message_status, cleaned_content = _normalize_bot_message(message, index, top_level_plan)
            normalized.append(bot_message)
            latest_status = message_status
            if first_bot_message == "":
                first_bot_message = cleaned_content

    return normalized, latest_status, first_bot_message


def _normalize_error_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "skill": str(item.get("skill", "")).strip(),
                "type": str(item.get("type", "")).strip(),
                "message": str(item.get("message", "")).strip(),
                "retryable": bool(item.get("retryable", False)),
            }
        )
    return normalized


def _normalize_continue_count(value: Any, messages: list[dict[str, Any]]) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = -1
    if count >= 0:
        return count
    return sum(1 for message in messages if message.get("role") == "user" and message.get("message_kind") == "continue_command")


def _normalize_agent_result(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Agent output must be a JSON object.")

    translation_plan = normalize_translation_plan_payload(payload.get("translation_plan"))
    if translation_plan is None:
        raise ValueError("translation_plan is missing or invalid.")

    normalized_messages, latest_message_status, derived_first_bot_message = _normalize_messages(
        payload.get("messages"),
        translation_plan,
    )
    if latest_message_status is None:
        raise ValueError("messages must contain at least one bot reply.")

    translation_status = normalize_translation_status_payload(payload.get("translation_status"))
    if translation_status is None:
        raise ValueError("translation_status is missing or invalid.")
    if translation_status != latest_message_status:
        raise ValueError("Top-level translation_status must match the latest bot payload status.")

    first_bot_message = derived_first_bot_message
    if not first_bot_message:
        first_bot_message = str(payload.get("first_bot_message", ""))

    return {
        "ok": True,
        "messages": normalized_messages,
        "first_bot_message": first_bot_message,
        "continue_count_used": _normalize_continue_count(payload.get("continue_count_used"), normalized_messages),
        "translation_plan": translation_plan,
        "translation_status": translation_status,
        "errors": _normalize_error_list(payload.get("errors")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="self-translate-full-paper-skill bridge runner")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    try:
        request = _read_json(args.input_json)
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_input", f"Failed to parse input json: {exc}"))
        return 1

    try:
        artifact_path = _require_string_path(request.get("agent_output_json"), "agent_output_json")
        artifact = _read_json(str(artifact_path))
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_input", str(exc)))
        return 1

    try:
        result = _normalize_agent_result(artifact)
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_output", str(exc)))
        return 1

    _write_json(args.output_json, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
