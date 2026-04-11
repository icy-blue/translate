from __future__ import annotations

import json
import re
from typing import Any

from ..platform.config import settings
from ..platform.models import Message
from .message_kinds import BOT_MESSAGE_KIND, LEGACY_INITIAL_PROMPTS, infer_message_kind, role_from_message_kind

TRANSLATION_PLAN_PROTOCOL = "unit_v1"
TRANSLATION_PLAN_STATUSES = {"ok", "unsupported"}
TRANSLATION_RESULT_STATES = {"OK", "UNSUPPORTED"}
TRANSLATION_STATES = {"IN_PROGRESS", "BODY_DONE", "ALL_DONE", "UNSUPPORTED"}
TRANSLATION_SCOPES = {"body", "appendix", "done"}
TRANSLATION_GLOSSARY_PROTOCOL = "glossary_v1"
TRANSLATION_GLOSSARY_STATUSES = {"draft", "confirmed"}
TRANSLATION_GLOSSARY_MAX_ENTRIES = 30
TRANSLATION_GLOSSARY_MAX_CANDIDATES = 3
TRANSLATION_STATUS_JSON_PATTERN = re.compile(
    r"\[TRANSLATION_STATUS_JSON\]\s*(\{.*?\})\s*\[/TRANSLATION_STATUS_JSON\]",
    re.DOTALL,
)
GENERIC_WRAPPER_TITLES = {
    "appendix",
    "appendices",
    "supplementary material",
    "supplementary materials",
    "supplementary",
    "supplemental material",
    "supplemental materials",
    "supplemental",
}
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


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def _unique_unit_ids(values: Any) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    if not isinstance(values, list):
        return ordered
    for value in values:
        unit_id = str(value or "").strip()
        if not unit_id or unit_id in seen:
            continue
        seen.add(unit_id)
        ordered.append(unit_id)
    return ordered


def _normalize_scope(value: Any) -> str:
    scope = str(value or "").strip().lower()
    return scope if scope in TRANSLATION_SCOPES else ""


def _normalize_wrapper_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _is_generic_wrapper_heading(unit_id: str) -> bool:
    return _normalize_wrapper_title(unit_id) in GENERIC_WRAPPER_TITLES


def _extract_hierarchy_prefix(unit_id: str) -> str:
    unit = str(unit_id or "").strip()
    if not unit or "::" in unit:
        return ""
    appendix_match = re.match(r"^(appendix\s+[a-z0-9]+(?:\.\d+)*)\b", unit, flags=re.IGNORECASE)
    if appendix_match:
        return appendix_match.group(1).lower()
    letter_child_match = re.match(r"^([a-z](?:\.\d+)+)\b", unit, flags=re.IGNORECASE)
    if letter_child_match:
        return letter_child_match.group(1).lower()
    letter_parent_match = re.match(r"^([a-z])\.\s+", unit, flags=re.IGNORECASE)
    if letter_parent_match:
        return letter_parent_match.group(1).lower()
    numeric_match = re.match(r"^(\d+(?:\.\d+)*)\b", unit)
    if numeric_match:
        return numeric_match.group(1).lower()
    roman_match = re.match(r"^([ivxlcdm]+)\.\s+", unit, flags=re.IGNORECASE)
    if roman_match:
        return roman_match.group(1).lower()
    return ""


def _sanitize_unit_hierarchy(unit_ids: list[str]) -> list[str]:
    units = list(unit_ids)
    if len(units) <= 1:
        return units

    split_parent_drop = {
        unit_id
        for unit_id in units
        if any(other.startswith(f"{unit_id} :: ") for other in units if other != unit_id)
    }
    if split_parent_drop:
        units = [unit_id for unit_id in units if unit_id not in split_parent_drop]

    if len(units) > 1:
        non_wrapper_units = [unit_id for unit_id in units if not _is_generic_wrapper_heading(unit_id)]
        if non_wrapper_units:
            units = non_wrapper_units

    prefixes = {unit_id: _extract_hierarchy_prefix(unit_id) for unit_id in units}
    descendant_drop: set[str] = set()
    for parent in units:
        parent_prefix = prefixes[parent]
        if not parent_prefix:
            continue
        for child in units:
            if child == parent:
                continue
            child_prefix = prefixes[child]
            if child_prefix and child_prefix.startswith(f"{parent_prefix}."):
                descendant_drop.add(child)
    if descendant_drop:
        units = [unit_id for unit_id in units if unit_id not in descendant_drop]
    return units


def build_initial_translation_prompt(template: str) -> str:
    return str(template or "").strip()


def _normalize_translation_glossary_entries(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    ordered: list[dict[str, Any]] = []
    seen_terms: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        term = str(value.get("term", "")).strip()
        if not term or term in seen_terms:
            continue
        raw_candidates = value.get("candidates") if isinstance(value.get("candidates"), list) else []
        candidates: list[str] = []
        seen_candidates: set[str] = set()
        for candidate in raw_candidates:
            normalized_candidate = str(candidate or "").strip()
            if not normalized_candidate or normalized_candidate in seen_candidates:
                continue
            seen_candidates.add(normalized_candidate)
            candidates.append(normalized_candidate)
            if len(candidates) >= TRANSLATION_GLOSSARY_MAX_CANDIDATES:
                break
        if not candidates:
            continue
        selected = str(value.get("selected", "")).strip()
        if selected not in candidates:
            selected = candidates[0]
        seen_terms.add(term)
        ordered.append({"term": term, "candidates": candidates, "selected": selected})
        if len(ordered) >= TRANSLATION_GLOSSARY_MAX_ENTRIES:
            break
    return ordered


def _extract_planning_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    translation_plan_payload = payload.get("translation_plan") if isinstance(payload.get("translation_plan"), dict) else payload
    glossary_payload = payload.get("translation_glossary", payload.get("glossary"))
    if isinstance(glossary_payload, list):
        glossary_payload = {"status": "draft", "entries": glossary_payload}
    elif not isinstance(glossary_payload, dict):
        glossary_payload = None
    return {
        "translation_plan": translation_plan_payload,
        "translation_glossary": glossary_payload,
    }


def build_unit_translation_prompt(
    template: str,
    *,
    active_units: list[str],
    current_unit_id: str,
    translation_glossary: dict[str, Any] | None = None,
) -> str:
    prompt = str(template or "").strip()
    prompt = prompt.replace("<<ACTIVE_UNITS_JSON>>", json.dumps(active_units, ensure_ascii=False))
    prompt = prompt.replace("<<CURRENT_UNIT_ID>>", str(current_unit_id or "").strip())
    normalized_glossary = normalize_translation_glossary_payload(translation_glossary)
    glossary_for_prompt = []
    if normalized_glossary is not None and normalized_glossary["status"] == "confirmed":
        glossary_for_prompt = [
            {"term": entry["term"], "translation": entry["selected"]}
            for entry in normalized_glossary["entries"]
        ]
    prompt = prompt.replace("<<CONFIRMED_GLOSSARY_JSON>>", json.dumps(glossary_for_prompt, ensure_ascii=False))
    return prompt.strip()


def normalize_translation_plan_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status", "")).strip().lower()
    if status not in TRANSLATION_PLAN_STATUSES:
        return None
    units = _sanitize_unit_hierarchy(_unique_unit_ids(payload.get("units")))
    appendix_units = [
        unit_id
        for unit_id in _sanitize_unit_hierarchy(_unique_unit_ids(payload.get("appendix_units")))
        if unit_id not in units
    ]
    reason = str(payload.get("reason", "")).strip()
    normalized = {
        "protocol": TRANSLATION_PLAN_PROTOCOL,
        "status": status,
        "units": units if status == "ok" else [],
        "appendix_units": appendix_units if status == "ok" else [],
        "reason": "" if status == "ok" else reason,
    }
    if normalized["status"] == "ok" and not normalized["units"]:
        normalized["status"] = "unsupported"
        normalized["reason"] = reason or "no_supported_units"
    return normalized


def parse_translation_plan_response(content: str | None) -> dict[str, Any] | None:
    text = _strip_code_fences(content or "")
    if not text:
        return None
    parsed = _extract_planning_payload(safe_json_loads(text, None))
    return normalize_translation_plan_payload(parsed.get("translation_plan"))


def normalize_translation_glossary_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        payload = {"entries": payload}
    if not isinstance(payload, dict):
        return None
    entries = _normalize_translation_glossary_entries(payload.get("entries"))
    status = str(payload.get("status", "")).strip().lower() or ("confirmed" if not entries else "draft")
    if status not in TRANSLATION_GLOSSARY_STATUSES:
        status = "confirmed" if not entries else "draft"
    if not entries:
        status = "confirmed"
    return {
        "protocol": TRANSLATION_GLOSSARY_PROTOCOL,
        "status": status,
        "entries": entries,
    }


def parse_translation_glossary_response(content: str | None) -> dict[str, Any] | None:
    text = _strip_code_fences(content or "")
    if not text:
        return None
    parsed = _extract_planning_payload(safe_json_loads(text, None))
    normalized = normalize_translation_glossary_payload(parsed.get("translation_glossary"))
    if normalized is not None:
        return normalized
    return normalize_translation_glossary_payload({"status": "confirmed", "entries": []})


def normalize_raw_translation_result_payload(payload: Any) -> dict[str, str] | None:
    if not isinstance(payload, dict):
        return None
    state = str(payload.get("state", "")).strip().upper()
    if state not in TRANSLATION_RESULT_STATES:
        return None
    return {
        "current_unit_id": str(payload.get("current_unit_id", "")).strip(),
        "state": state,
        "reason": str(payload.get("reason", "")).strip(),
    }


def parse_raw_translation_status_block(content: str | None) -> dict[str, str] | None:
    text = content or ""
    match = TRANSLATION_STATUS_JSON_PATTERN.search(text)
    if not match:
        return None
    return normalize_raw_translation_result_payload(safe_json_loads(match.group(1).strip(), None))


def extract_raw_translation_status_text(content: str | None) -> str | None:
    text = content or ""
    match = TRANSLATION_STATUS_JSON_PATTERN.search(text)
    return match.group(0).strip() if match else None


def strip_translation_status_block(content: str | None) -> str:
    return TRANSLATION_STATUS_JSON_PATTERN.sub("", content or "").strip()


def _normalize_completed_ids(value: Any, all_unit_ids: list[str]) -> list[str]:
    completed_set = set(_unique_unit_ids(value))
    return [unit_id for unit_id in all_unit_ids if unit_id in completed_set]


def build_translation_status_payload(
    translation_plan: dict[str, Any],
    *,
    completed_unit_ids: list[str],
    current_unit_id: str = "",
    attempted_scope: str = "body",
    raw_translation_result: dict[str, str] | None = None,
    source: str = "canonical_payload",
) -> dict[str, Any]:
    normalized_plan = normalize_translation_plan_payload(translation_plan) or {
        "protocol": TRANSLATION_PLAN_PROTOCOL,
        "status": "unsupported",
        "units": [],
        "appendix_units": [],
        "reason": "missing_translation_plan",
    }
    body_units = list(normalized_plan["units"])
    appendix_units = list(normalized_plan["appendix_units"])
    all_units = body_units + appendix_units
    completed_ids = _normalize_completed_ids(completed_unit_ids, all_units)
    body_remaining = [unit_id for unit_id in body_units if unit_id not in completed_ids]
    appendix_remaining = [unit_id for unit_id in appendix_units if unit_id not in completed_ids]
    attempted_scope = _normalize_scope(attempted_scope) or "body"
    current_unit_id = str(current_unit_id or "").strip()
    raw_result = normalize_raw_translation_result_payload(raw_translation_result)

    active_scope = "body"
    active_units = body_units
    remaining_unit_ids = body_remaining
    state = "IN_PROGRESS"
    reason = ""
    next_unit_id = body_remaining[0] if body_remaining else ""

    if normalized_plan["status"] == "unsupported":
        active_scope = "body"
        active_units = []
        remaining_unit_ids = []
        state = "UNSUPPORTED"
        reason = normalized_plan["reason"]
        current_unit_id = ""
        next_unit_id = ""
    elif raw_result and raw_result["state"] == "UNSUPPORTED":
        active_scope = attempted_scope if attempted_scope in {"body", "appendix"} else ("body" if body_remaining else "appendix")
        active_units = body_units if active_scope == "body" else appendix_units
        remaining_unit_ids = body_remaining if active_scope == "body" else appendix_remaining
        state = "UNSUPPORTED"
        reason = raw_result["reason"]
        current_unit_id = raw_result["current_unit_id"] or current_unit_id
        next_unit_id = ""
    elif body_remaining:
        active_scope = "body"
        active_units = body_units
        remaining_unit_ids = body_remaining
        state = "IN_PROGRESS"
        next_unit_id = body_remaining[0]
    elif appendix_remaining:
        active_scope = "appendix"
        active_units = appendix_units
        remaining_unit_ids = appendix_remaining
        state = "BODY_DONE"
        next_unit_id = appendix_remaining[0]
    else:
        active_scope = "done"
        active_units = []
        remaining_unit_ids = []
        state = "ALL_DONE"
        next_unit_id = ""

    current_unit_index = active_units.index(current_unit_id) if current_unit_id in active_units else -1
    return {
        "protocol": TRANSLATION_PLAN_PROTOCOL,
        "planner_status": normalized_plan["status"],
        "active_scope": active_scope,
        "active_units": active_units,
        "current_unit_id": current_unit_id,
        "current_unit_index": current_unit_index,
        "completed_unit_ids": completed_ids,
        "remaining_unit_ids": remaining_unit_ids,
        "next_unit_id": next_unit_id,
        "state": state,
        "reason": reason,
        "total_unit_count": len(all_units),
        "completed_unit_count": len(completed_ids),
        "source": str(source or "").strip() or "canonical_payload",
        "is_completed": state in {"BODY_DONE", "ALL_DONE"},
        "is_all_done": state == "ALL_DONE",
    }


def normalize_translation_status_payload(status: Any) -> dict[str, Any] | None:
    if not isinstance(status, dict):
        return None
    protocol = str(status.get("protocol", "")).strip() or TRANSLATION_PLAN_PROTOCOL
    planner_status = str(status.get("planner_status", "")).strip().lower()
    state = str(status.get("state", "")).strip().upper()
    active_scope = _normalize_scope(status.get("active_scope"))
    if protocol != TRANSLATION_PLAN_PROTOCOL or planner_status not in TRANSLATION_PLAN_STATUSES or state not in TRANSLATION_STATES:
        return None

    active_units = _unique_unit_ids(status.get("active_units"))
    completed_unit_ids = _unique_unit_ids(status.get("completed_unit_ids"))
    remaining_unit_ids = _unique_unit_ids(status.get("remaining_unit_ids"))
    current_unit_id = str(status.get("current_unit_id", "")).strip()
    next_unit_id = str(status.get("next_unit_id", "")).strip()
    try:
        current_unit_index = int(status.get("current_unit_index", -1))
    except (TypeError, ValueError):
        current_unit_index = -1
    try:
        total_unit_count = int(status.get("total_unit_count", len(completed_unit_ids) + len(remaining_unit_ids)))
    except (TypeError, ValueError):
        total_unit_count = len(completed_unit_ids) + len(remaining_unit_ids)
    try:
        completed_unit_count = int(status.get("completed_unit_count", len(completed_unit_ids)))
    except (TypeError, ValueError):
        completed_unit_count = len(completed_unit_ids)

    normalized = {
        "protocol": protocol,
        "planner_status": planner_status,
        "active_scope": active_scope,
        "active_units": active_units,
        "current_unit_id": current_unit_id,
        "current_unit_index": current_unit_index if current_unit_index >= 0 else -1,
        "completed_unit_ids": completed_unit_ids,
        "remaining_unit_ids": remaining_unit_ids,
        "next_unit_id": next_unit_id,
        "state": state,
        "reason": str(status.get("reason", "")).strip(),
        "total_unit_count": max(0, total_unit_count),
        "completed_unit_count": max(0, completed_unit_count),
        "source": str(status.get("source", "")).strip() or "canonical_payload",
        "is_completed": state in {"BODY_DONE", "ALL_DONE"},
        "is_all_done": state == "ALL_DONE",
    }
    return normalized


def preprocess_bot_reply_for_storage(content: str | None, client_payload: Any = None) -> dict[str, Any]:
    original_content = content or ""
    existing_payload = _safe_payload_dict(client_payload)
    payload = {
        key: value
        for key, value in existing_payload.items()
        if key not in {"translation_status", "translation_plan", "translation_glossary", *LEGACY_TRANSLATION_PAYLOAD_KEYS}
    }
    translation_plan = normalize_translation_plan_payload(existing_payload.get("translation_plan"))
    translation_status = normalize_translation_status_payload(existing_payload.get("translation_status"))
    translation_glossary = normalize_translation_glossary_payload(existing_payload.get("translation_glossary"))
    raw_translation_result = parse_raw_translation_status_block(original_content)
    clean_content = strip_translation_status_block(original_content) if raw_translation_result is not None else original_content.strip()
    if translation_plan is not None:
        payload["translation_plan"] = translation_plan
    else:
        payload.pop("translation_plan", None)
    if translation_status is not None:
        payload["translation_status"] = translation_status
    else:
        payload.pop("translation_status", None)
    if translation_glossary is not None:
        payload["translation_glossary"] = translation_glossary
    else:
        payload.pop("translation_glossary", None)
    return {
        "content": clean_content,
        "client_payload": payload or None,
        "translation_plan": translation_plan,
        "translation_status": translation_status,
        "translation_glossary": translation_glossary,
        "raw_translation_result": raw_translation_result,
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
