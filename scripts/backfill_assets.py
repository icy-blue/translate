#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from sqlmodel import Session, SQLModel, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import crud
from app import _ensure_asset_columns
from database import engine
from models import FileRecord
from pdf_figures import extract_pdf_figures, extract_pdf_tables


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill figure/table assets for existing papers by downloading PDFs from stored Poe URLs."
    )
    parser.add_argument(
        "--cache-dir",
        default=str(ROOT_DIR / "_temp" / "pdf_cache"),
        help="Directory for caching downloaded PDFs.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Ignore any cached PDF and download it again.",
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
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds for each PDF download.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retry count for PDF downloads.",
    )
    parser.add_argument(
        "--pause",
        type=float,
        default=0.0,
        help="Optional pause in seconds between records.",
    )
    return parser.parse_args()


def get_cache_path(cache_dir: Path, file_record: FileRecord) -> Path:
    url_digest = hashlib.sha256(file_record.poe_url.encode("utf-8")).hexdigest()[:12]
    filename_stem = Path(file_record.filename or file_record.conversation_id or "paper").stem
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in filename_stem).strip("_")
    if not safe_stem:
        safe_stem = file_record.conversation_id or "paper"
    return cache_dir / f"{safe_stem}-{file_record.conversation_id}-{url_digest}.pdf"


def download_pdf(url: str, timeout: int, retries: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "translate-backfill/1.0",
            "Accept": "application/pdf,*/*",
        },
    )

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(min(2.0 * attempt, 5.0))

    raise RuntimeError(f"Failed to download PDF from {url}: {last_error}")


def get_pdf_bytes(
    file_record: FileRecord,
    cache_dir: Path,
    timeout: int,
    retries: int,
    refresh_cache: bool,
) -> tuple[bytes, bool, Path]:
    cache_path = get_cache_path(cache_dir, file_record)
    if cache_path.exists() and not refresh_cache:
        return cache_path.read_bytes(), True, cache_path

    pdf_bytes = download_pdf(file_record.poe_url, timeout=timeout, retries=retries)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(pdf_bytes)
    return pdf_bytes, False, cache_path


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
    cache_dir: Path,
    timeout: int,
    retries: int,
    refresh_cache: bool,
) -> tuple[int, int, bool, Path]:
    pdf_bytes, cache_hit, cache_path = get_pdf_bytes(
        file_record,
        cache_dir=cache_dir,
        timeout=timeout,
        retries=retries,
        refresh_cache=refresh_cache,
    )
    figures = extract_pdf_figures(pdf_bytes)
    tables = extract_pdf_tables(pdf_bytes)
    crud.replace_figures(session, file_record.conversation_id, figures)
    crud.replace_tables(session, file_record.conversation_id, tables)
    return len(figures), len(tables), cache_hit, cache_path


def main() -> int:
    args = parse_args()
    cache_dir = Path(args.cache_dir).expanduser()

    SQLModel.metadata.create_all(engine)
    _ensure_asset_columns()

    with Session(engine) as session:
        records = get_file_records(session, args)

        if not records:
            print("No matching file records found.")
            return 0

        print(f"Processing {len(records)} record(s)...")
        success_count = 0
        failure_count = 0

        records = records[::-1]

        for index, record in enumerate(records, start=1):
            label = record.conversation_id
            if record.filename:
                label = f"{record.conversation_id} ({record.filename})"

            print(f"[{index}/{len(records)}] {label}")
            try:
                figure_count, table_count, cache_hit, cache_path = backfill_record(
                    session,
                    record,
                    cache_dir=cache_dir,
                    timeout=args.timeout,
                    retries=args.retries,
                    refresh_cache=args.refresh_cache,
                )
                success_count += 1
                cache_status = "cache" if cache_hit else "download"
                print(f"  ok: {figure_count} figure(s), {table_count} table(s) [{cache_status}: {cache_path}]")
            except Exception as exc:
                session.rollback()
                failure_count += 1
                print(f"  failed: {exc}")

            if args.pause > 0 and index < len(records):
                time.sleep(args.pause)

        print(f"Done. success={success_count}, failed={failure_count}")
        return 0 if failure_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
