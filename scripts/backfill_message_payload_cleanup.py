#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.database import engine
from backend.domains.message_kinds import BOT_MESSAGE_KIND
from backend.persistence.models import Message
from backend.services.message_utils import (
    extract_raw_translation_status_text,
    parse_document_outline_block,
    preprocess_bot_reply_for_storage,
)

DEFAULT_REPORT_PATH = ROOT_DIR / "data" / "message_payload_cleanup_backfill_report.jsonl"


@dataclass
class AuditRow:
    message_id: int
    conversation_id: str
    action: str
    parse_errors: list[str]
    has_translation_status_block: bool
    has_document_outline_block: bool
    original_length: int
    next_length: int
    original_payload_json: str | None
    next_payload_json: str | None
    original_content: str
    next_content: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill cleaned bot message content plus translation_status/document_outline payload."
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
    return json.dumps(value, ensure_ascii=False)


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


def build_audit_row(message: Message) -> AuditRow:
    original_content = message.content or ""
    prepared = preprocess_bot_reply_for_storage(original_content, message.client_payload_json)
    next_content = str(prepared["content"])
    next_payload_json = _json_dumps(prepared["client_payload"])
    parse_errors = list(prepared["parse_errors"])
    has_outline = bool(parse_document_outline_block(original_content))
    has_status = bool(extract_raw_translation_status_text(original_content))

    if parse_errors:
        action = "skip_parse_error"
    elif next_content != original_content or next_payload_json != message.client_payload_json:
        action = "update"
    else:
        action = "keep"

    return AuditRow(
        message_id=message.id or 0,
        conversation_id=message.conversation_id,
        action=action,
        parse_errors=parse_errors,
        has_translation_status_block=has_status,
        has_document_outline_block=has_outline,
        original_length=len(original_content),
        next_length=len(next_content),
        original_payload_json=message.client_payload_json,
        next_payload_json=next_payload_json,
        original_content=original_content,
        next_content=next_content,
    )


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
            row = build_audit_row(message)
            rows.append(row)
            if not args.write or row.action != "update":
                continue

            message.content = row.next_content
            message.client_payload_json = row.next_payload_json
            session.add(message)
            updated += 1

        if args.write and updated:
            session.commit()

    write_report(rows, "-" if str(args.output) == "-" else args.output)
    print(f"processed={len(rows)} updated={updated}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
