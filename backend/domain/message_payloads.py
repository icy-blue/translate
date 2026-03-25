from __future__ import annotations

import json
import re
from typing import Any

from ..platform.config import settings
from ..platform.models import Message
from .message_kinds import BOT_MESSAGE_KIND, LEGACY_INITIAL_PROMPTS, infer_message_kind, role_from_message_kind

TRANSLATION_STATUS_PATTERN = re.compile(r"\[TRANSLATION_STATUS\]\s*(.*?)\s*\[/TRANSLATION_STATUS\]", re.DOTALL)
COMMAND_BLOCK_PATTERN = re.compile(r"\[COMMAND\]\s*(.*?)\s*\[/COMMAND\]", re.DOTALL)
TRANSLATION_STATUS_KEYS = (
    "scope",
    "completed",
    "current",
    "next",
    "remaining",
    "state",
    "phase",
    "available_scope_extensions",
    "next_action_type",
    "next_action_command",
    "next_action_target_scope",
    "recommended_stop_reason",
)
SCOPE_EXTENSION_ORDER = ("appendix", "acknowledgements", "references")
SCOPE_EXTENSION_COMMANDS = {
    "appendix": "继续翻译附录",
    "acknowledgements": "继续翻译致谢",
    "references": "继续翻译参考文献",
}
TRANSLATION_PHASES = {"body", "appendix", "acknowledgements", "references", "done"}
NEXT_ACTION_TYPES = {"continue", "stop"}
LEGACY_TRANSLATION_PAYLOAD_KEYS = {
    "document_outline",
    "raw_translation_status_text",
    "raw_document_outline_text",
    "parse_error",
}


def safe_json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _safe_payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = safe_json_loads(value, {})
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _normalize_scope_extension_name(value: str | None) -> str | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    if text in {"appendix", "appendices"}:
        return "appendix"
    if text in {"acknowledgement", "acknowledgements", "acknowledgment", "acknowledgments"}:
        return "acknowledgements"
    if text in {"reference", "references"}:
        return "references"
    if text in {"body", "main_body", "body_only"}:
        return "body"
    if text in {"done", "all_done", "none"}:
        return "done" if text != "none" else "none"
    if "附录" in text or "补充材料" in text or "supplement" in text:
        return "appendix"
    if "致谢" in text or "acknowledg" in text:
        return "acknowledgements"
    if "参考文献" in text or "references" in text or "bibliograph" in text:
        return "references"
    if "正文" in text or "主体" in text:
        return "body"
    return None


def _normalize_scope_extensions(values: list[str]) -> list[str]:
    ordered: list[str] = []
    for scope in values:
        normalized = _normalize_scope_extension_name(scope)
        if normalized not in SCOPE_EXTENSION_ORDER or normalized in ordered:
            continue
        ordered.append(normalized)
    return [scope for scope in SCOPE_EXTENSION_ORDER if scope in ordered]


def _parse_scope_extension_list(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple, set)):
        return _normalize_scope_extensions([str(item) for item in raw])
    text = str(raw or "").strip()
    if not text:
        return []
    parts = [part.strip() for part in re.split(r"[,\|;/，、]+", text) if part.strip()]
    return _normalize_scope_extensions(parts)


def build_command_block(action: str, target_scope: str) -> str:
    return f"[COMMAND]\naction={action}\ntarget={target_scope}\n[/COMMAND]"


def build_input_status_block(status: dict[str, Any]) -> str:
    return (
        "[INPUT_STATUS]\n"
        f"scope={str(status.get('scope', '')).strip()}\n"
        f"completed={str(status.get('completed', '')).strip()}\n"
        f"current={str(status.get('current', '')).strip()}\n"
        f"next={str(status.get('next', '')).strip()}\n"
        f"remaining={str(status.get('remaining', '')).strip()}\n"
        f"state={str(status.get('state', '')).strip()}\n"
        f"phase={str(status.get('phase', '')).strip()}\n"
        "[/INPUT_STATUS]"
    )


def build_initial_translation_prompt(template: str) -> str:
    return str(template or "").strip()


def build_continue_translation_prompt(template: str, *, translation_status: dict[str, Any], action: str, target_scope: str) -> str:
    command_block = build_command_block(action=action, target_scope=target_scope)
    input_status_block = build_input_status_block(translation_status)
    prompt = str(template or "").strip()
    return prompt.replace("<<INPUT_STATUS_BLOCK>>", input_status_block).replace("<<COMMAND_BLOCK>>", command_block).strip()


def _parse_command_block(raw_command: str | None) -> dict[str, str]:
    text = str(raw_command or "").strip()
    if not text:
        return {}
    match = COMMAND_BLOCK_PATTERN.search(text)
    if not match:
        return {}
    result: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip().lower()
        if normalized_key in {"action", "target"}:
            result[normalized_key] = value.strip()
    return result


def normalize_translation_status_payload(status: Any) -> dict[str, Any] | None:
    if not isinstance(status, dict):
        return None
    normalized = dict(status)
    state = str(normalized.get("state", "")).strip().upper()
    if not state:
        return None
    phase = _normalize_scope_extension_name(str(normalized.get("phase", "")).strip())
    if phase not in TRANSLATION_PHASES:
        phase = ""
    available_scope_extensions = _parse_scope_extension_list(normalized.get("available_scope_extensions"))
    explicit_next_action = normalized.get("next_action") if isinstance(normalized.get("next_action"), dict) else {}
    raw_next_action_command = str(explicit_next_action.get("command", normalized.get("next_action_command", ""))).strip()
    command_fields = _parse_command_block(raw_next_action_command)
    next_action_type = str(explicit_next_action.get("type", normalized.get("next_action_type", command_fields.get("action", "")))).strip().lower()
    if next_action_type not in NEXT_ACTION_TYPES:
        next_action_type = ""
    next_action_target_scope = _normalize_scope_extension_name(
        str(explicit_next_action.get("target_scope", normalized.get("next_action_target_scope", command_fields.get("target", "")))).strip()
    )
    if next_action_type == "stop":
        next_action_target_scope = "none"
    elif next_action_target_scope == "none":
        next_action_target_scope = ""
    next_action_command = raw_next_action_command
    if next_action_type and (next_action_target_scope or next_action_type == "stop"):
        next_action_command = build_command_block(next_action_type, next_action_target_scope or "none")

    normalized["scope"] = str(normalized.get("scope", "")).strip()
    normalized["completed"] = str(normalized.get("completed", "")).strip()
    normalized["current"] = str(normalized.get("current", "")).strip()
    normalized["next"] = str(normalized.get("next", "")).strip()
    normalized["remaining"] = str(normalized.get("remaining", "")).strip()
    normalized["state"] = state
    normalized["phase"] = phase
    normalized["available_scope_extensions"] = available_scope_extensions
    normalized["extension_commands"] = {scope: SCOPE_EXTENSION_COMMANDS[scope] for scope in available_scope_extensions}
    normalized["next_action"] = {
        "type": next_action_type,
        "command": next_action_command,
        "target_scope": next_action_target_scope or ("none" if next_action_type == "stop" else ""),
    }
    normalized["recommended_stop_reason"] = str(normalized.get("recommended_stop_reason", "")).strip().lower()
    normalized["source"] = str(normalized.get("source", "")).strip() or "canonical_payload"
    normalized["is_completed"] = state in {"BODY_DONE", "ALL_DONE"}
    normalized["is_all_done"] = state == "ALL_DONE"
    return normalized


def parse_raw_translation_status_block(content: str | None) -> dict[str, Any] | None:
    text = content or ""
    match = TRANSLATION_STATUS_PATTERN.search(text)
    if not match:
        return None
    payload: dict[str, Any] = {key: "" for key in TRANSLATION_STATUS_KEYS}
    current_key: str | None = None
    current_buffer: list[str] = []

    def flush_current_buffer() -> None:
        nonlocal current_key, current_buffer
        if current_key is None:
            return
        payload[current_key] = "\n".join(current_buffer).strip()
        current_key = None
        current_buffer = []

    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line:
            if current_key == "next_action_command":
                current_buffer.append("")
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            normalized_key = key.strip().lower()
            if normalized_key in TRANSLATION_STATUS_KEYS:
                flush_current_buffer()
                current_key = normalized_key
                current_buffer = [value.strip() if normalized_key != "next_action_command" else value.rstrip()]
                continue
        if current_key == "next_action_command":
            current_buffer.append(raw_line.rstrip())
            continue
    flush_current_buffer()
    raw_available_scope_extensions = str(payload.get("available_scope_extensions", "")).strip()
    payload["available_scope_extensions"] = [part.strip() for part in re.split(r"[,\|;/，、]+", raw_available_scope_extensions) if part.strip()]
    payload["next_action"] = {
        "type": str(payload.get("next_action_type", "")).strip(),
        "command": str(payload.get("next_action_command", "")).strip(),
        "target_scope": str(payload.get("next_action_target_scope", "")).strip(),
    }
    return normalize_translation_status_payload(payload)


def extract_raw_translation_status_text(content: str | None) -> str | None:
    text = content or ""
    match = TRANSLATION_STATUS_PATTERN.search(text)
    return match.group(0).strip() if match else None


def strip_translation_status_block(content: str | None) -> str:
    return TRANSLATION_STATUS_PATTERN.sub("", content or "").strip()


def preprocess_bot_reply_for_storage(content: str | None, client_payload: Any = None) -> dict[str, Any]:
    original_content = content or ""
    existing_payload = _safe_payload_dict(client_payload)
    payload = {
        key: value
        for key, value in existing_payload.items()
        if key not in {"translation_status", *LEGACY_TRANSLATION_PAYLOAD_KEYS}
    }
    translation_status = normalize_translation_status_payload(existing_payload.get("translation_status"))
    raw_status_text = extract_raw_translation_status_text(original_content)
    if raw_status_text:
        parsed_status = parse_raw_translation_status_block(original_content)
        if parsed_status is not None:
            translation_status = parsed_status
    clean_content = strip_translation_status_block(original_content) if raw_status_text and translation_status is not None else original_content.strip()
    if translation_status is not None:
        payload["translation_status"] = translation_status
    else:
        payload.pop("translation_status", None)
    return {
        "content": clean_content,
        "client_payload": payload or None,
        "translation_status": translation_status,
    }


def infer_message_metadata(
    message: Message | None = None,
    *,
    message_kind: str | None = None,
    role: str | None = None,
    message_type: str | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    actual_content = content if content is not None else (message.content if message else "")
    actual_message_kind = infer_message_kind(
        message_kind=message_kind if message_kind is not None else (getattr(message, "message_kind", None) if message else None),
        message_type=message_type,
        role=role,
        content=actual_content,
        initial_prompts=(settings.initial_prompt, *LEGACY_INITIAL_PROMPTS),
    )
    return {
        "message_kind": actual_message_kind,
        "role": role_from_message_kind(actual_message_kind),
        "visible_to_user": actual_message_kind in {BOT_MESSAGE_KIND, "user_message"},
    }
