#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlmodel import Session, SQLModel, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.modules.assets import download_pdf_bytes
from backend.platform.config import engine
from backend.platform.local_files import build_pdf_url, local_file_url_to_path, write_pdf_file
from backend.platform.models import FileRecord


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill stored PDF URLs into local /files storage.")
    parser.add_argument("--conversation-id", help="Only backfill a single conversation.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N matching records.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N matching records.")
    parser.add_argument("--dry-run", action="store_true", help="Report intended changes without writing files or updating DB.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing local files.")
    parser.add_argument(
        "--clear-unrecoverable",
        action="store_true",
        help="Clear poe_url when the old source is known to be unrecoverable, such as text:// or HTTP 403.",
    )
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds for old remote URLs.")
    return parser.parse_args()


def get_file_records(session: Session, args: argparse.Namespace) -> list[FileRecord]:
    statement = select(FileRecord).order_by(FileRecord.uploaded_at)
    if args.conversation_id:
        statement = statement.where(FileRecord.conversation_id == args.conversation_id)
    if args.offset:
        statement = statement.offset(max(0, args.offset))
    if args.limit is not None:
        statement = statement.limit(max(0, args.limit))
    return session.exec(statement).all()


def backfill_record(
    session: Session,
    file_record: FileRecord,
    *,
    timeout: int,
    overwrite: bool,
    dry_run: bool,
    clear_unrecoverable: bool = False,
) -> str:
    target_url = build_pdf_url(file_record.conversation_id, file_record.id)
    if not file_record.poe_url:
        return "skipped-empty"
    target_path = local_file_url_to_path(target_url)
    if file_record.poe_url == target_url and target_path is not None and target_path.exists() and not overwrite:
        return "skipped-existing"
    if dry_run and clear_unrecoverable and is_unrecoverable_source(file_record.poe_url):
        return "would-clear"
    if dry_run:
        return "would-update"

    try:
        pdf_bytes = download_pdf_bytes(file_record.poe_url, timeout=timeout)
    except RuntimeError as exc:
        if clear_unrecoverable and is_unrecoverable_source(file_record.poe_url, exc):
            if dry_run:
                return "would-clear"
            file_record.poe_url = ""
            session.add(file_record)
            session.commit()
            return "cleared"
        raise
    write_pdf_file(file_record.conversation_id, file_record.id, pdf_bytes, overwrite=overwrite)
    file_record.poe_url = target_url
    file_record.content_type = file_record.content_type or "application/pdf"
    file_record.poe_name = file_record.poe_name or file_record.filename
    session.add(file_record)
    session.commit()
    return "updated"


def is_unrecoverable_source(url: str, exc: RuntimeError | None = None) -> bool:
    if not url or url.startswith("text://"):
        return True
    message = str(exc or "")
    return "HTTP Error 403" in message or "Forbidden" in message


def main() -> int:
    args = parse_args()
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        records = get_file_records(session, args)
        if not records:
            print("No matching file records found.", flush=True)
            return 0

        print(f"Processing {len(records)} record(s)...", flush=True)
        counts = {
            "updated": 0,
            "would-update": 0,
            "skipped-existing": 0,
            "skipped-empty": 0,
            "cleared": 0,
            "would-clear": 0,
            "failed": 0,
        }
        for index, record in enumerate(records, start=1):
            label = f"{record.conversation_id}/{record.id} ({record.filename})"
            print(f"[{index}/{len(records)}] {label}", flush=True)
            try:
                status = backfill_record(
                    session,
                    record,
                    timeout=args.timeout,
                    overwrite=args.overwrite,
                    dry_run=args.dry_run,
                    clear_unrecoverable=args.clear_unrecoverable,
                )
                counts[status] = counts.get(status, 0) + 1
                print(f"  {status}: {build_pdf_url(record.conversation_id, record.id)}", flush=True)
            except Exception as exc:
                session.rollback()
                counts["failed"] += 1
                print(f"  failed: {exc}", flush=True)

        print(
            "Done. "
            f"updated={counts['updated']} "
            f"would_update={counts['would-update']} "
            f"skipped_existing={counts['skipped-existing']} "
            f"skipped_empty={counts['skipped-empty']} "
            f"cleared={counts['cleared']} "
            f"would_clear={counts['would-clear']} "
            f"failed={counts['failed']}",
            flush=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
