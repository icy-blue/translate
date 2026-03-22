#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from sqlalchemy import desc
from sqlmodel import Session, SQLModel, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import crud
from database import engine
from models import FileRecord, PaperSemanticScholarResult


SEMANTIC_SCHOLAR_MATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
SEMANTIC_SCHOLAR_FIELDS = ",".join(
    [
        "paperId",
        "corpusId",
        "title",
        "year",
        "authors",
        "venue",
        "publicationVenue",
        "externalIds",
        "url",
        "abstract",
        "referenceCount",
        "citationCount",
        "influentialCitationCount",
        "publicationTypes",
        "publicationDate",
        "journal",
        "isOpenAccess",
        "openAccessPdf",
    ]
)


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


def get_existing_result(
    session: Session, conversation_id: str
) -> PaperSemanticScholarResult | None:
    statement = select(PaperSemanticScholarResult).where(
        PaperSemanticScholarResult.conversation_id == conversation_id
    )
    return session.exec(statement).first()


def build_request(title: str, api_key: str | None) -> urllib.request.Request:
    query_string = urllib.parse.urlencode(
        {
            "query": title,
            "fields": SEMANTIC_SCHOLAR_FIELDS,
        }
    )
    headers = {
        "Accept": "application/json",
        "User-Agent": "translate-backfill-semantic-scholar/1.0",
    }
    if api_key:
        headers["x-api-key"] = api_key
    return urllib.request.Request(f"{SEMANTIC_SCHOLAR_MATCH_URL}?{query_string}", headers=headers)


def dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def get_retry_delay(exc: HTTPError | URLError, attempt: int) -> float:
    if isinstance(exc, HTTPError):
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
    return min(30.0, 2 ** attempt)


def fetch_semantic_scholar_match(
    title: str,
    api_key: str | None,
    timeout: float,
    max_retries: int,
) -> dict[str, Any]:
    request = build_request(title, api_key)

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            should_retry = exc.code in {429, 500, 502, 503, 504} and attempt < max_retries
            if should_retry:
                delay = get_retry_delay(exc, attempt)
                print(f"  retry: HTTP {exc.code}, waiting {delay:.1f}s")
                time.sleep(delay)
                continue
            raise RuntimeError(f"HTTP {exc.code}: {body or exc.reason}") from exc
        except URLError as exc:
            if attempt < max_retries:
                delay = get_retry_delay(exc, attempt)
                print(f"  retry: network error, waiting {delay:.1f}s")
                time.sleep(delay)
                continue
            raise RuntimeError(f"Network error: {exc.reason}") from exc

    raise RuntimeError("Unexpected retry loop exit.")


def build_result_payload(
    conversation_id: str,
    response_payload: dict[str, Any],
) -> dict[str, Any]:
    items = response_payload.get("data")
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected response payload: {response_payload!r}")

    matched_paper = items[0] if items else None
    payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "status": "matched" if matched_paper else "not_found",
        "paper_id": None,
        "corpus_id": None,
        "matched_title": None,
        "url": None,
        "abstract": None,
        "year": None,
        "venue": None,
        "publication_date": None,
        "is_open_access": None,
        "match_score": None,
        "citation_count": None,
        "reference_count": None,
        "authors_json": None,
        "external_ids_json": None,
        "publication_types_json": None,
        "publication_venue_json": None,
        "journal_json": None,
        "open_access_pdf_json": None,
        "raw_response_json": dump_json(response_payload) or "{}",
        "source": "semantic_scholar",
    }

    if matched_paper:
        payload.update(
            {
                "paper_id": matched_paper.get("paperId"),
                "corpus_id": matched_paper.get("corpusId"),
                "matched_title": matched_paper.get("title"),
                "url": matched_paper.get("url"),
                "abstract": matched_paper.get("abstract"),
                "year": matched_paper.get("year"),
                "venue": matched_paper.get("venue"),
                "publication_date": matched_paper.get("publicationDate"),
                "is_open_access": matched_paper.get("isOpenAccess"),
                "match_score": matched_paper.get("matchScore"),
                "citation_count": matched_paper.get("citationCount"),
                "reference_count": matched_paper.get("referenceCount"),
                "authors_json": dump_json(matched_paper.get("authors")),
                "external_ids_json": dump_json(matched_paper.get("externalIds")),
                "publication_types_json": dump_json(matched_paper.get("publicationTypes")),
                "publication_venue_json": dump_json(matched_paper.get("publicationVenue")),
                "journal_json": dump_json(matched_paper.get("journal")),
                "open_access_pdf_json": dump_json(matched_paper.get("openAccessPdf")),
            }
        )

    return payload


def upsert_result(session: Session, payload: dict[str, Any]) -> PaperSemanticScholarResult:
    existing = get_existing_result(session, payload["conversation_id"])
    now = datetime.now(timezone.utc)

    if existing is None:
        result = PaperSemanticScholarResult(**payload, created_at=now, updated_at=now)
        session.add(result)
    else:
        result = existing
        for key, value in payload.items():
            setattr(result, key, value)
        result.updated_at = now

    session.commit()
    return result


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
            conversation = crud.get_conversation(session, record.conversation_id)
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
                response_payload = fetch_semantic_scholar_match(
                    title=title,
                    api_key=args.api_key,
                    timeout=args.timeout,
                    max_retries=max(0, args.max_retries),
                )
                payload = build_result_payload(record.conversation_id, response_payload)
                upsert_result(session, payload)

                if payload["status"] == "matched":
                    matched_count += 1
                    print(
                        "  ok:"
                        f" {payload['matched_title'] or 'unknown title'}"
                        f" | score={payload['match_score']}"
                        f" | paperId={payload['paper_id']}"
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
