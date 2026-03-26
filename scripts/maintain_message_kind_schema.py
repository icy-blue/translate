#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.domain.message_kinds import LEGACY_INITIAL_PROMPTS, infer_message_kind, role_from_message_kind
from backend.domain.message_sections import classify_message_section
from backend.platform.config import engine, settings
from scripts.backfill_legacy_display_filter import apply_current_display_strategy


DEFAULT_REPORT_PATH = ROOT_DIR / "data" / "message_kind_maintenance_plan.jsonl"
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
SCOPE_EXTENSION_ORDER = ("appendix", "acknowledgements", "references")
SCOPE_EXTENSION_COMMANDS = {
    "appendix": "继续翻译附录",
    "acknowledgements": "继续翻译致谢",
    "references": "继续翻译参考文献",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill message_kind/section_category/visible_to_user/client_payload_json from legacy role/message_type data and optionally drop legacy columns."
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually write message_kind/section_category/visible_to_user/client_payload_json updates. Default is dry-run.",
    )
    parser.add_argument(
        "--drop-legacy-columns",
        action="store_true",
        help="After a successful write, also drop legacy message.role and message.message_type columns when present.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"Write a JSONL report here. Use '-' to print to stdout. Default: {DEFAULT_REPORT_PATH}",
    )
    return parser.parse_args()


def _safe_json_loads(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


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


def _normalize_explicit_translation_status(status: dict[str, Any]) -> dict[str, Any] | None:
    normalized = dict(status)
    state = str(normalized.get("state", "")).strip().upper()
    if not state:
        return None

    phase = _normalize_scope_extension_name(str(normalized.get("phase", "")).strip())
    if phase not in {"body", "appendix", "acknowledgements", "references", "done"}:
        phase = ""

    available_scope_extensions = _parse_scope_extension_list(normalized.get("available_scope_extensions"))
    explicit_next_action = normalized.get("next_action") if isinstance(normalized.get("next_action"), dict) else {}
    next_action_type = str(
        explicit_next_action.get("type", normalized.get("next_action_type", ""))
    ).strip().lower()
    next_action_command = str(
        explicit_next_action.get("command", normalized.get("next_action_command", ""))
    ).strip()
    next_action_target_scope = _normalize_scope_extension_name(
        str(explicit_next_action.get("target_scope", normalized.get("next_action_target_scope", ""))).strip()
    )

    normalized["state"] = state
    normalized["phase"] = phase
    normalized["available_scope_extensions"] = available_scope_extensions
    normalized["extension_commands"] = {
        scope: SCOPE_EXTENSION_COMMANDS[scope]
        for scope in available_scope_extensions
    }
    normalized["next_action"] = {
        "type": next_action_type,
        "command": next_action_command,
        "target_scope": next_action_target_scope or "",
    }
    normalized["recommended_stop_reason"] = str(normalized.get("recommended_stop_reason", "")).strip()
    normalized["source"] = "status_block"
    normalized["is_completed"] = state in {"BODY_DONE", "ALL_DONE"}
    normalized["is_all_done"] = state == "ALL_DONE"
    return normalized


def _parse_translation_status(content: str | None) -> dict[str, Any] | None:
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
        key = key.strip().lower()
        if key not in TRANSLATION_STATUS_KEYS:
            continue
        payload[key] = value.strip()
    return _normalize_explicit_translation_status(payload)


def _build_message_client_payload(content: str, existing_payload_json: str | None) -> dict[str, Any] | None:
    payload = _safe_json_loads(existing_payload_json) or {}
    existing_status = payload.get("translation_status") if isinstance(payload.get("translation_status"), dict) else None
    parsed_status = _parse_translation_status(content)
    if parsed_status:
        payload["translation_status"] = parsed_status
    elif existing_status and str(existing_status.get("source", "")).strip().lower() != "heuristic":
        normalized_status = _normalize_explicit_translation_status(existing_status)
        if normalized_status:
            payload["translation_status"] = normalized_status
        else:
            payload.pop("translation_status", None)
    else:
        payload.pop("translation_status", None)
    return payload or None


def _extract_translation_status(client_payload_json: str | None) -> dict[str, Any] | None:
    payload = _safe_json_loads(client_payload_json)
    if not isinstance(payload, dict):
        return None
    status = payload.get("translation_status")
    return status if isinstance(status, dict) else None


def _write_report(rows: list[dict[str, Any]], output: Path | str) -> None:
    if output == "-":
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    args = parse_args()

    report_rows: list[dict[str, Any]] = []

    with engine.begin() as connection:
        inspector = inspect(connection)
        existing_columns = {column["name"] for column in inspector.get_columns("message")}

        missing_required_columns = {
            column_name: column_type
            for column_name, column_type in {
                "message_kind": "VARCHAR",
                "section_category": "VARCHAR",
                "visible_to_user": "BOOLEAN",
                "client_payload_json": "TEXT",
            }.items()
            if column_name not in existing_columns
        }
        if args.write:
            for column_name, column_type in missing_required_columns.items():
                connection.execute(text(f"ALTER TABLE message ADD COLUMN {column_name} {column_type}"))
            existing_columns.update(missing_required_columns)

        select_columns = [
            "id",
            "content",
        ]
        if "message_kind" in existing_columns:
            select_columns.append("message_kind")
        if "section_category" in existing_columns:
            select_columns.append("section_category")
        if "visible_to_user" in existing_columns:
            select_columns.append("visible_to_user")
        if "client_payload_json" in existing_columns:
            select_columns.append("client_payload_json")
        if "role" in existing_columns:
            select_columns.append("role")
        if "message_type" in existing_columns:
            select_columns.append("message_type")

        rows = connection.execute(text(f"SELECT {', '.join(select_columns)} FROM message ORDER BY id")).fetchall()
        updates: list[dict[str, Any]] = []

        for row in rows:
            legacy_role = getattr(row, "role", None) if "role" in existing_columns else None
            legacy_message_type = getattr(row, "message_type", None) if "message_type" in existing_columns else None
            current_message_kind = getattr(row, "message_kind", None) if "message_kind" in existing_columns else None
            current_section_category = getattr(row, "section_category", None) if "section_category" in existing_columns else None
            current_visible_to_user = getattr(row, "visible_to_user", None) if "visible_to_user" in existing_columns else None
            content = getattr(row, "content", "") or ""
            client_payload_json = getattr(row, "client_payload_json", None) if "client_payload_json" in existing_columns else None

            next_message_kind = infer_message_kind(
                message_kind=current_message_kind,
                message_type=legacy_message_type,
                role=legacy_role,
                content=content,
                initial_prompts=(settings.initial_prompt, *LEGACY_INITIAL_PROMPTS),
            )
            if role_from_message_kind(next_message_kind) == "bot":
                next_client_payload = _build_message_client_payload(content, client_payload_json)
                next_client_payload_json = _json_dumps(next_client_payload) if next_client_payload else None
            else:
                next_client_payload_json = client_payload_json
            translation_status = _extract_translation_status(next_client_payload_json)
            display_content = apply_current_display_strategy(content).display_content
            if role_from_message_kind(next_message_kind) == "bot":
                next_section_category = classify_message_section(
                    original_content=content,
                    display_content=display_content,
                    translation_status=translation_status,
                ).get("section_category")
            else:
                next_section_category = None
            next_visible_to_user = (
                current_visible_to_user
                if current_visible_to_user is not None
                else (role_from_message_kind(next_message_kind) == "bot" or next_message_kind == "user_message")
            )

            report_row = {
                "message_id": row.id,
                "message_kind_before": current_message_kind,
                "message_kind_after": next_message_kind,
                "section_category_before": current_section_category,
                "section_category_after": next_section_category,
                "visible_to_user_before": current_visible_to_user,
                "visible_to_user_after": next_visible_to_user,
                "client_payload_json_before": client_payload_json,
                "client_payload_json_after": next_client_payload_json,
                "legacy_role": legacy_role,
                "legacy_message_type": legacy_message_type,
            }
            report_rows.append(report_row)

            if not args.write:
                continue

            if (
                current_message_kind != next_message_kind
                or current_section_category != next_section_category
                or current_visible_to_user != next_visible_to_user
                or client_payload_json != next_client_payload_json
            ):
                updates.append(
                    {
                        "id": row.id,
                        "message_kind": next_message_kind,
                        "section_category": next_section_category,
                        "visible_to_user": bool(next_visible_to_user),
                        "client_payload_json": next_client_payload_json,
                    }
                )

        if args.write and updates:
            connection.execute(
                    text(
                        "UPDATE message "
                        "SET message_kind = :message_kind, "
                        "section_category = :section_category, "
                        "visible_to_user = :visible_to_user, "
                        "client_payload_json = :client_payload_json "
                        "WHERE id = :id"
                    ),
                    updates,
            )

        if args.write and args.drop_legacy_columns:
            for column_name in ("role", "message_type"):
                if column_name in existing_columns:
                    connection.execute(text(f"ALTER TABLE message DROP COLUMN {column_name}"))

    _write_report(report_rows, "-" if str(args.output) == "-" else args.output)

    mode = "WRITE" if args.write else "DRY-RUN"
    dropped = "yes" if args.write and args.drop_legacy_columns else "no"
    destination = "stdout" if str(args.output) == "-" else str(args.output)
    print(f"{mode}: rows={len(report_rows)}, dropped_legacy_columns={dropped}, report={destination}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
