#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.domain.message_kinds import BOT_MESSAGE_KIND
from backend.domain.message_payloads import (
    normalize_document_outline_payload,
    parse_document_outline_block,
    safe_json_loads,
)
from backend.platform.config import engine
from backend.platform.models import Message

DEFAULT_REPORT_PATH = ROOT_DIR / "data" / "translation_payload_v2_backfill_report.jsonl"
TRANSLATION_STATUS_PATTERN = re.compile(
    r"\[TRANSLATION_STATUS\]\s*(.*?)\s*\[/TRANSLATION_STATUS\]",
    re.DOTALL,
)
COMMAND_BLOCK_PATTERN = re.compile(
    r"\[COMMAND\]\s*(.*?)\s*\[/COMMAND\]",
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
SCOPE_EXTENSION_ORDER = ("appendix", "acknowledgements", "references")
SCOPE_EXTENSION_COMMANDS = {
    "appendix": "继续翻译附录",
    "acknowledgements": "继续翻译致谢",
    "references": "继续翻译参考文献",
}
LEGACY_TRANSIENT_KEYS = {
    "raw_translation_status_text",
    "raw_document_outline_text",
    "parse_error",
}
VALID_STATES = {"IN_PROGRESS", "BODY_DONE", "ALL_DONE"}
VALID_NEXT_ACTIONS = {"continue", "custom_message", "stop"}


@dataclass
class AuditRow:
    message_id: int
    conversation_id: str
    action: str
    reason: str
    before_payload_json: str | None
    after_payload_json: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill bot message client_payload_json to canonical translation payload v2."
    )
    parser.add_argument("--write", action="store_true", help="Actually update the database.")
    parser.add_argument("--conversation-id", help="Only backfill one conversation.")
    parser.add_argument("--message-id", type=int, help="Only backfill one message.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N matched rows.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N matched rows.")
    parser.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="asc",
        help="Message id ordering for matched rows.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"Write a JSONL report here. Use '-' for stdout. Default: {DEFAULT_REPORT_PATH}",
    )
    return parser.parse_args()


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_payload_dict(raw: str | None) -> dict[str, Any]:
    parsed = safe_json_loads(raw, {})
    return dict(parsed) if isinstance(parsed, dict) else {}


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


def _build_command_block(action: str, target_scope: str) -> str:
    return f"[COMMAND]\naction={action}\ntarget={target_scope}\n[/COMMAND]"


def _parse_command_block(raw_command: str | None) -> dict[str, str]:
    text = str(raw_command or "").strip()
    if not text:
        return {}
    match = COMMAND_BLOCK_PATTERN.search(text)
    if not match:
        return {}

    parsed: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip().lower()
        if normalized_key in {"action", "target"}:
            parsed[normalized_key] = value.strip()
    return parsed


def _infer_target_scope_from_command_text(command: str | None) -> str:
    text = str(command or "").strip()
    if not text:
        return ""
    normalized = text.lower()
    if "附录" in text or "appendix" in normalized or "supplement" in normalized:
        return "appendix"
    if "致谢" in text or "acknowledg" in normalized:
        return "acknowledgements"
    if "参考文献" in text or "references" in normalized or "bibliograph" in normalized:
        return "references"
    if "继续" in text or "continue" in normalized:
        return "body"
    return ""


def _parse_legacy_status_block(content: str | None) -> dict[str, Any] | None:
    text = content or ""
    match = TRANSLATION_STATUS_PATTERN.search(text)
    if not match:
        return None

    payload: dict[str, Any] = {key: "" for key in TRANSLATION_STATUS_KEYS}
    current_key: str | None = None
    current_buffer: list[str] = []

    def flush_current() -> None:
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
                flush_current()
                current_key = normalized_key
                current_buffer = [value.strip() if normalized_key != "next_action_command" else value.rstrip()]
                continue
        if current_key == "next_action_command":
            current_buffer.append(raw_line.rstrip())
    flush_current()
    return payload


def extract_candidate_status(message: Message) -> tuple[dict[str, Any] | None, str]:
    payload = _safe_payload_dict(message.client_payload_json)
    existing_status = payload.get("translation_status")
    if isinstance(existing_status, dict):
        return dict(existing_status), "payload.translation_status"

    parsed_status = _parse_legacy_status_block(message.content)
    if parsed_status:
        return parsed_status, "content.translation_status_block"
    return None, "missing"


def normalize_status_to_v2(status: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(status, dict):
        return None, "missing_translation_status"

    raw_command = str(
        (
            status.get("next_action", {}).get("command")
            if isinstance(status.get("next_action"), dict)
            else status.get("next_action_command", "")
        )
        or ""
    ).strip()
    command_fields = _parse_command_block(raw_command)
    explicit_next_action = status.get("next_action") if isinstance(status.get("next_action"), dict) else {}

    state = str(status.get("state", "")).strip().upper()
    if state not in VALID_STATES:
        return None, "invalid_state"

    phase = _normalize_scope_extension_name(str(status.get("phase", "")).strip()) or ""
    if phase == "none":
        phase = ""

    available_scope_extensions = _parse_scope_extension_list(status.get("available_scope_extensions"))
    next_action_type = str(
        explicit_next_action.get("type", status.get("next_action_type", command_fields.get("action", "")))
    ).strip().lower()
    if next_action_type == "custom_message":
        next_action_type = "continue"
    if next_action_type and next_action_type not in VALID_NEXT_ACTIONS:
        return None, "invalid_next_action_type"

    next_action_target_scope = _normalize_scope_extension_name(
        str(
            explicit_next_action.get(
                "target_scope",
                status.get("next_action_target_scope", command_fields.get("target", "")),
            )
        ).strip()
    ) or ""
    if next_action_target_scope == "none":
        next_action_target_scope = ""

    if not next_action_target_scope and raw_command:
        next_action_target_scope = _infer_target_scope_from_command_text(raw_command)

    if not next_action_type:
        if state == "IN_PROGRESS":
            next_action_type = "continue"
            next_action_target_scope = next_action_target_scope or (phase if phase in {"body", "appendix", "acknowledgements", "references"} else "body")
        else:
            next_action_type = "stop"

    if next_action_type == "continue" and not next_action_target_scope:
        return None, "missing_target_scope"

    canonical_command = (
        _build_command_block(next_action_type, next_action_target_scope or "none")
        if next_action_type in {"continue", "stop"}
        else raw_command
    )

    normalized = {
        "scope": str(status.get("scope", "")).strip() or "body_only",
        "completed": str(status.get("completed", "")).strip(),
        "current": str(status.get("current", "")).strip(),
        "next": str(status.get("next", "")).strip(),
        "remaining": str(status.get("remaining", "")).strip(),
        "state": state,
        "phase": phase,
        "available_scope_extensions": available_scope_extensions,
        "next_action": {
            "type": next_action_type,
            "command": canonical_command,
            "target_scope": next_action_target_scope or ("none" if next_action_type == "stop" else ""),
        },
        "extension_commands": {
            scope: SCOPE_EXTENSION_COMMANDS[scope]
            for scope in available_scope_extensions
        },
        "recommended_stop_reason": str(status.get("recommended_stop_reason", "")).strip().lower(),
        "source": "backfill_v2",
        "is_completed": state in {"BODY_DONE", "ALL_DONE"},
        "is_all_done": state == "ALL_DONE",
    }
    return normalized, "ok"


def build_next_payload(message: Message) -> tuple[dict[str, Any] | None, str]:
    payload = _safe_payload_dict(message.client_payload_json)
    preserved_payload = {
        key: value
        for key, value in payload.items()
        if key not in {"translation_status", "document_outline", *LEGACY_TRANSIENT_KEYS}
    }

    candidate_status, status_source = extract_candidate_status(message)
    normalized_status, status_reason = normalize_status_to_v2(candidate_status)
    if normalized_status is None:
        return None, f"{status_source}:{status_reason}"

    next_payload = dict(preserved_payload)
    next_payload["translation_status"] = normalized_status

    existing_outline = normalize_document_outline_payload(payload.get("document_outline"))
    clean_content = TRANSLATION_STATUS_PATTERN.sub("", message.content or "").strip()
    outline_result = parse_document_outline_block(clean_content)
    parsed_outline = (
        normalize_document_outline_payload(outline_result.get("document_outline"))
        if outline_result and outline_result.get("parsed")
        else None
    )
    document_outline = existing_outline or parsed_outline
    if document_outline is not None:
        next_payload["document_outline"] = document_outline
    else:
        next_payload.pop("document_outline", None)

    return next_payload or None, "ok"


def build_statement(args: argparse.Namespace):
    order_column = desc(Message.id) if args.order == "desc" else Message.id
    statement = (
        select(Message)
        .where(Message.message_kind == BOT_MESSAGE_KIND)
        .order_by(order_column)
    )
    if args.conversation_id:
        statement = statement.where(Message.conversation_id == args.conversation_id)
    if args.message_id is not None:
        statement = statement.where(Message.id == args.message_id)
    if args.offset:
        statement = statement.offset(max(0, args.offset))
    if args.limit is not None:
        statement = statement.limit(max(0, args.limit))
    return statement


def write_report(rows: list[AuditRow], output: Path | str) -> None:
    rendered = "".join(json.dumps(asdict(row), ensure_ascii=False) + "\n" for row in rows)
    if str(output) == "-":
        sys.stdout.write(rendered)
        return

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")


def main() -> int:
    args = parse_args()
    updated = 0
    rows: list[AuditRow] = []

    with Session(engine) as session:
        messages = session.exec(build_statement(args)).all()
        for message in messages:
            before_payload_json = _json_dumps(_safe_payload_dict(message.client_payload_json)) if message.client_payload_json else None
            next_payload, reason = build_next_payload(message)
            after_payload_json = _json_dumps(next_payload)

            if next_payload is None:
                action = "skip_error"
            elif before_payload_json == after_payload_json:
                action = "keep"
            else:
                action = "update"

            rows.append(
                AuditRow(
                    message_id=message.id or 0,
                    conversation_id=message.conversation_id,
                    action=action,
                    reason=reason,
                    before_payload_json=before_payload_json,
                    after_payload_json=after_payload_json,
                )
            )

            if not args.write or action != "update":
                continue

            message.client_payload_json = after_payload_json
            session.add(message)
            updated += 1

        if args.write and updated:
            session.commit()

    write_report(rows, "-" if str(args.output) == "-" else args.output)
    print(f"processed={len(rows)} updated={updated}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
