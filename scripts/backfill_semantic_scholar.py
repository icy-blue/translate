#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from sqlalchemy import desc
from sqlmodel import Session, SQLModel, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.database import engine
from backend.integrations.semantic_scholar import refresh_semantic_scholar_result
from backend.modules.conversations import get_conversation
from backend.persistence.models import FileRecord, PaperSemanticScholarResult


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Semantic Scholar title-search results for existing PDF conversations."
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("SEMANTIC_SCHOLAR_API_KEY") or os.getenv("S2_API_KEY"),
        help="Semantic Scholar API key. Optional; if omitted, the script auto-slows requests.",
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
        help="Include conversations that already have Semantic Scholar matches and overwrite them.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=None,
        help="Pause in seconds between requests. Defaults to 1.0 without an API key, otherwise 0.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds for each request.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=4,
        help="Maximum retry attempts for 429/5xx/network errors.",
    )
    parser.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="desc",
        help="Processing order for matched records. 'asc' keeps upload order; 'desc' processes newest first.",
    )
    return parser.parse_args()


def resolve_pause(args: argparse.Namespace) -> float:
    if args.pause is not None:
        return max(0.0, args.pause)
    return 1.0 if not args.api_key else 0.0


def get_file_records(session: Session, args: argparse.Namespace) -> list[FileRecord]:
    order_column = desc(FileRecord.uploaded_at) if args.order == "desc" else FileRecord.uploaded_at
    statement = select(FileRecord).order_by(order_column)

    if not args.all_records:
        existing_subquery = select(PaperSemanticScholarResult.conversation_id)
        statement = statement.where(~FileRecord.conversation_id.in_(existing_subquery))

    if args.conversation_id:
        statement = statement.where(FileRecord.conversation_id == args.conversation_id)

    if args.offset:
        statement = statement.offset(max(0, args.offset))

    if args.limit is not None:
        statement = statement.limit(max(0, args.limit))

    return session.exec(statement).all()

def main() -> int:
    args = parse_args()
    pause_seconds = resolve_pause(args)

    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        records = get_file_records(session, args)
        if not records:
            print("No matching file records found.")
            return 0

        print(f"Processing {len(records)} record(s)...")
        if not args.api_key:
            print(f"No API key provided; using conservative pause={pause_seconds:.1f}s between requests.")

        matched_count = 0
        not_found_count = 0
        failure_count = 0

        for index, record in enumerate(records, start=1):
            conversation = get_conversation(session, record.conversation_id)
            title = (conversation.title or "").strip() if conversation else ""
            label = record.conversation_id
            if title:
                label = f"{record.conversation_id} ({title})"

            print(f"[{index}/{len(records)}] {label}")

            if not title:
                failure_count += 1
                print("  failed: missing conversation title")
                continue

            try:
                result = refresh_semantic_scholar_result(
                    session=session,
                    conversation_id=record.conversation_id,
                    title=title,
                    api_key=args.api_key,
                    timeout=args.timeout,
                    max_retries=max(0, args.max_retries),
                )

                if result.status == "matched":
                    matched_count += 1
                    print(
                        "  ok:"
                        f" {result.matched_title or 'unknown title'}"
                        f" | score={result.match_score}"
                        f" | paperId={result.paper_id}"
                    )
                else:
                    not_found_count += 1
                    print("  ok: no match found")
            except Exception as exc:
                session.rollback()
                failure_count += 1
                print(f"  failed: {exc}")

            if pause_seconds > 0 and index < len(records):
                time.sleep(pause_seconds)

        print(
            "Done."
            f" matched={matched_count},"
            f" not_found={not_found_count},"
            f" failed={failure_count}"
        )
        return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
