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
    build_translation_status_payload,
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


def _clean_error_list(value: Any) -> list[dict[str, Any]]:
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


def _build_user_message(unit_id: str, *, index: int) -> dict[str, Any]:
    return {
        "role": "user",
        "content": f"Translate {unit_id} from the local PDF." if index == 0 else f"Continue with {unit_id}.",
        "message_kind": "system_prompt" if index == 0 else "continue_command",
        "visible_to_user": False,
    }


def _build_unsupported_planner_messages(
    translation_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    translation_status = build_translation_status_payload(translation_plan, completed_unit_ids=[])
    bot_payload = preprocess_bot_reply_for_storage(
        "",
        {"translation_plan": translation_plan, "translation_status": translation_status},
    )
    messages = [
        {
            "role": "user",
            "content": "Inspect the local PDF and build a translation plan.",
            "message_kind": "system_prompt",
            "visible_to_user": False,
        },
        {
            "role": "bot",
            "content": "",
            "message_kind": "bot_reply",
            "visible_to_user": True,
            "section_category": None,
            "client_payload": bot_payload["client_payload"],
        },
    ]
    return messages, translation_status, ""


def _normalize_unit_result(value: Any, index: int) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"unit_results[{index}] must be an object.")
    unit_id = str(value.get("unit_id", "")).strip()
    state = str(value.get("state", "")).strip().upper()
    reason = str(value.get("reason", "")).strip()
    content = str(value.get("content", ""))
    if not unit_id:
        raise ValueError(f"unit_results[{index}].unit_id is required.")
    if state not in {"OK", "UNSUPPORTED"}:
        raise ValueError(f"unit_results[{index}].state must be OK or UNSUPPORTED.")
    if state == "OK" and not content.strip():
        raise ValueError(f"unit_results[{index}].content is required when state=OK.")
    if state == "UNSUPPORTED" and not reason:
        raise ValueError(f"unit_results[{index}].reason is required when state=UNSUPPORTED.")
    return {
        "unit_id": unit_id,
        "state": state,
        "reason": reason,
        "content": content,
    }


def _materialize_agent_artifact(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Build input must be a JSON object.")

    translation_plan = normalize_translation_plan_payload(payload.get("translation_plan"))
    if translation_plan is None:
        raise ValueError("translation_plan is missing or invalid.")
    errors = _clean_error_list(payload.get("errors"))

    if translation_plan["status"] == "unsupported":
        messages, latest_status, first_bot_message = _build_unsupported_planner_messages(translation_plan)
        return {
            "messages": messages,
            "first_bot_message": first_bot_message,
            "continue_count_used": 0,
            "translation_plan": translation_plan,
            "translation_status": latest_status,
            "errors": errors,
        }

    raw_unit_results = payload.get("unit_results")
    if not isinstance(raw_unit_results, list) or not raw_unit_results:
        raise ValueError("unit_results must be a non-empty list when translation_plan.status=ok.")

    ordered_units = list(translation_plan["units"]) + list(translation_plan["appendix_units"])
    if len(raw_unit_results) > len(ordered_units):
        raise ValueError("unit_results cannot exceed the number of planned units.")

    messages: list[dict[str, Any]] = []
    completed_unit_ids: list[str] = []
    latest_status: dict[str, Any] | None = None
    first_bot_message = ""
    saw_unsupported = False

    for index, raw_result in enumerate(raw_unit_results):
        unit_result = _normalize_unit_result(raw_result, index)
        expected_unit_id = ordered_units[index]
        if unit_result["unit_id"] != expected_unit_id:
            raise ValueError(
                f"unit_results[{index}].unit_id must match the next planned unit: {expected_unit_id}."
            )
        if saw_unsupported:
            raise ValueError("unit_results cannot continue after an UNSUPPORTED unit.")

        unit_id = unit_result["unit_id"]
        scope = "appendix" if unit_id in translation_plan["appendix_units"] else "body"
        messages.append(_build_user_message(unit_id, index=index))

        completed_after_step = list(completed_unit_ids)
        if unit_result["state"] == "OK":
            completed_after_step.append(unit_id)
        raw_translation_result = {
            "current_unit_id": unit_id,
            "state": unit_result["state"],
            "reason": unit_result["reason"],
        }
        latest_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=completed_after_step,
            current_unit_id=unit_id,
            attempted_scope=scope,
            raw_translation_result=raw_translation_result,
        )
        bot_payload = preprocess_bot_reply_for_storage(
            unit_result["content"] if unit_result["state"] == "OK" else "",
            {"translation_plan": translation_plan, "translation_status": latest_status},
        )
        bot_content = str(bot_payload["content"])
        messages.append(
            {
                "role": "bot",
                "content": bot_content,
                "message_kind": "bot_reply",
                "visible_to_user": True,
                "section_category": None,
                "client_payload": bot_payload["client_payload"],
            }
        )
        if first_bot_message == "":
            first_bot_message = bot_content
        if unit_result["state"] == "OK":
            completed_unit_ids = completed_after_step
        else:
            saw_unsupported = True

    if latest_status is None:
        raise ValueError("Failed to build translation_status from unit_results.")

    continue_count_used = sum(
        1
        for message in messages
        if message["role"] == "user" and message["message_kind"] == "continue_command"
    )
    return {
        "messages": messages,
        "first_bot_message": first_bot_message,
        "continue_count_used": continue_count_used,
        "translation_plan": translation_plan,
        "translation_status": latest_status,
        "errors": errors,
    }


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
        "errors": _clean_error_list(payload.get("errors")),
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

    mode = str(request.get("mode", "")).strip().lower() or "bridge"
    if mode == "build_artifact":
        try:
            result = _materialize_agent_artifact(request)
        except Exception as exc:
            _write_json(args.output_json, _result_error("invalid_input", str(exc)))
            return 1
        _write_json(args.output_json, result)
        return 0

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
