from __future__ import annotations

import json
import re
from typing import Any

from ..core.config import settings
from ..domains.message_kinds import (
    BOT_MESSAGE_KIND,
    LEGACY_INITIAL_PROMPTS,
    infer_message_kind,
    role_from_message_kind,
)
from ..persistence.models import Message

TRANSLATION_STATUS_PATTERN = re.compile(
    r"\[TRANSLATION_STATUS\]\s*(.*?)\s*\[/TRANSLATION_STATUS\]",
    re.DOTALL,
)
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


def safe_json_loads(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def normalize_message_text(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "")


def parse_raw_translation_status_block(content: str | None) -> dict[str, Any] | None:
    text = content or ""
    match = TRANSLATION_STATUS_PATTERN.search(text)
    if not match:
        return None

    payload: dict[str, Any] = {key: "" for key in TRANSLATION_STATUS_KEYS}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip().lower()
        if normalized_key not in TRANSLATION_STATUS_KEYS:
            continue
        payload[normalized_key] = value.strip()

    raw_available_scope_extensions = str(payload.get("available_scope_extensions", "")).strip()
    payload["available_scope_extensions"] = [
        part.strip()
        for part in re.split(r"[,\|;/，、]+", raw_available_scope_extensions)
        if part.strip()
    ]
    payload["next_action"] = {
        "type": str(payload.get("next_action_type", "")).strip(),
        "command": str(payload.get("next_action_command", "")).strip(),
        "target_scope": str(payload.get("next_action_target_scope", "")).strip(),
    }

    return payload or None


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
