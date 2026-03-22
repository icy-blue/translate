#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

from sqlalchemy import desc
from sqlmodel import Session, SQLModel, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import backend.crud as crud
from backend.config import settings
from backend.database import engine
from backend.models import FileRecord, Message, PaperTag
from backend.paper_tags import extract_abstract_for_tagging
from backend.poe_utils import classify_paper_tags


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill paper tags for existing PDF conversations using title + first bot message."
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("POE_API_KEY"),
        help="Poe API key. Defaults to POE_API_KEY env var.",
    )
    parser.add_argument(
        "--poe-model",
        default=settings.poe_model,
        help="Poe bot/model used for compact tag classification.",
    )
    parser.add_argument(
        "--conversation-id",
        help="Only backfill a single conversation.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N matching records.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip the first N matching records.",
    )
    parser.add_argument(
        "--all-records",
        action="store_true",
        help="Include conversations that already have tags and recompute them.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help="Optional pause in seconds between records.",
    )
    parser.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="desc",
        help="Processing order for matched records. 'asc' keeps upload order; 'desc' processes newest matches first.",
    )
    return parser.parse_args()


def get_file_records(session: Session, args: argparse.Namespace) -> list[FileRecord]:
    order_column = desc(FileRecord.uploaded_at) if args.order == "desc" else FileRecord.uploaded_at
    statement = select(FileRecord).order_by(order_column)
    if not args.all_records:
        missing_tag_subquery = select(PaperTag.conversation_id)
        statement = statement.where(~FileRecord.conversation_id.in_(missing_tag_subquery))
    if args.conversation_id:
        statement = statement.where(FileRecord.conversation_id == args.conversation_id)
    if args.offset:
        statement = statement.offset(max(0, args.offset))
    if args.limit is not None:
        statement = statement.limit(max(0, args.limit))
    return session.exec(statement).all()


def get_first_bot_message(session: Session, conversation_id: str) -> Message | None:
    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id, Message.role == "bot")
        .order_by(Message.id)
    )
    return session.exec(statement).first()


async def backfill_record(
    session: Session,
    file_record: FileRecord,
    poe_model: str,
    api_key: str,
) -> list[dict]:
    conversation = crud.get_conversation(session, file_record.conversation_id)
    if conversation is None:
        raise RuntimeError("Conversation not found.")

    first_bot_message = get_first_bot_message(session, file_record.conversation_id)
    abstract = extract_abstract_for_tagging(first_bot_message.content if first_bot_message else "")
    if not conversation.title or not abstract:
        raise RuntimeError("Missing title or first bot abstract.")

    tags = await classify_paper_tags(conversation.title, abstract, poe_model, api_key)
    crud.replace_tags(session, file_record.conversation_id, tags)
    return tags


async def async_main() -> int:
    args = parse_args()
    if not args.api_key:
        print("Missing Poe API key. Pass --api-key or set POE_API_KEY.")
        return 2

    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        records = get_file_records(session, args)
        if not records:
            print("No matching file records found.")
            return 0

        print(f"Processing {len(records)} record(s)...")
        success_count = 0
        failure_count = 0

        for index, record in enumerate(records, start=1):
            conversation = crud.get_conversation(session, record.conversation_id)
            label = record.conversation_id
            if conversation and conversation.title:
                label = f"{record.conversation_id} ({conversation.title})"

            print(f"[{index}/{len(records)}] {label}")
            try:
                tags = await backfill_record(
                    session,
                    record,
                    poe_model=args.poe_model,
                    api_key=args.api_key,
                )
                success_count += 1
                print(f"  ok: {', '.join(tag['tag_code'] for tag in tags) or 'no tags'}")
            except Exception as exc:
                session.rollback()
                failure_count += 1
                print(f"  failed: {exc}")

            if args.pause > 0 and index < len(records):
                time.sleep(args.pause)

        print(f"Done. success={success_count}, failed={failure_count}")
        return 0 if failure_count == 0 else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
