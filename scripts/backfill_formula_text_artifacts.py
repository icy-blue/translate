#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.domain.message_kinds import BOT_MESSAGE_KIND
from backend.platform.config import engine
from backend.platform.models import Message

DEFAULT_REPORT_PATH = ROOT_DIR / "data" / "formula_text_artifacts_backfill_report.jsonl"
SLASH_U_PATTERN = re.compile(r"/u(1D[0-9A-Fa-f]{3}|[0-9A-Fa-f]{4})")
V0XK_PATTERN = re.compile(r"<\|v0xK\|([^|]+?)\|v0xK\|>")
SUMMATION_PATTERN = re.compile(r"/summation(?:display|text)(?:\.[0-9]+)?")


@dataclass
class TokenCounts:
    slash_u: int = 0
    v0xk: int = 0
    summation: int = 0
    barex: int = 0
    bardblex: int = 0
    alt_suffix: int = 0


@dataclass
class AuditRow:
    message_id: int
    conversation_id: str
    action: str
    original_length: int
    next_length: int
    original_counts: TokenCounts
    next_counts: TokenCounts
    original_content: str
    next_content: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill leaked formula text artifacts in visible bot replies.")
    parser.add_argument("--conversation-id", help="Only inspect one conversation.")
    parser.add_argument("--message-id", type=int, help="Only inspect one message.")
    parser.add_argument("--write", action="store_true", help="Actually update the database.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"Write a JSONL report here. Use '-' for stdout. Default: {DEFAULT_REPORT_PATH}",
    )
    return parser.parse_args()


def count_tokens(text: str) -> TokenCounts:
    return TokenCounts(
        slash_u=len(SLASH_U_PATTERN.findall(text)),
        v0xk=len(V0XK_PATTERN.findall(text)) + text.count("\\v0xK"),
        summation=len(SUMMATION_PATTERN.findall(text)),
        barex=text.count("/barex"),
        bardblex=text.count("/bardblex"),
        alt_suffix=text.count(".alt"),
    )


def decode_slash_unicode(text: str) -> str:
    return SLASH_U_PATTERN.sub(lambda match: chr(int(match.group(1), 16)), text)


def normalize_subscript_token(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if re.fullmatch(r"[A-Za-z0-9_+\-=,.' ∈≠]+", value):
        value = value.replace("−", "-")
        return f"_{{{value}}}"
    return f"_{{{value}}}"


def repair_v0xk_tokens(text: str) -> str:
    repaired = V0XK_PATTERN.sub(lambda match: normalize_subscript_token(match.group(1)), text)
    repaired = repaired.replace("\\v0xK", "")
    return repaired


def repair_text(text: str) -> str:
    repaired = text
    repaired = decode_slash_unicode(repaired)
    repaired = repair_v0xk_tokens(repaired)
    repaired = SUMMATION_PATTERN.sub("∑", repaired)
    repaired = repaired.replace("/bardblex", "‖")
    repaired = repaired.replace("/barex", "|")
    repaired = repaired.replace(".alt", "")
    repaired = re.sub(r" +([,，。.；;：:）)])", r"\1", repaired)
    repaired = re.sub(r"([(（]) +", r"\1", repaired)
    return repaired


def build_statement(args: argparse.Namespace):
    statement = (
        select(Message)
        .where(Message.message_kind == BOT_MESSAGE_KIND, Message.visible_to_user == True)
        .order_by(Message.id)
    )
    if args.conversation_id:
        statement = statement.where(Message.conversation_id == args.conversation_id)
    if args.message_id is not None:
        statement = statement.where(Message.id == args.message_id)
    return statement


def build_audit_row(message: Message) -> AuditRow:
    original_content = message.content or ""
    next_content = repair_text(original_content)
    original_counts = count_tokens(original_content)
    next_counts = count_tokens(next_content)
    if next_content != original_content and sum(asdict(next_counts).values()) < sum(asdict(original_counts).values()):
        action = "update"
    elif next_content != original_content:
        action = "skip_ambiguous"
    else:
        action = "keep"
    return AuditRow(
        message_id=message.id or 0,
        conversation_id=message.conversation_id,
        action=action,
        original_length=len(original_content),
        next_length=len(next_content),
        original_counts=original_counts,
        next_counts=next_counts,
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


def main_with_args(args: argparse.Namespace) -> int:
    rows: list[AuditRow] = []
    updated = 0
    with Session(engine) as session:
        messages = session.exec(build_statement(args)).all()
        for message in messages:
            row = build_audit_row(message)
            rows.append(row)
            if not args.write or row.action != "update":
                continue
            message.content = row.next_content
            session.add(message)
            updated += 1
        if args.write and updated:
            session.commit()
    write_report(rows, "-" if str(args.output) == "-" else args.output)
    print(f"processed={len(rows)} updated={updated}", file=sys.stderr)
    return 0


def main() -> int:
    return main_with_args(parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
