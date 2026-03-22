#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from sqlalchemy import desc
from sqlmodel import Session, SQLModel, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from database import engine
from models import Conversation, PaperSemanticScholarResult


DEFAULT_OUTPUT = Path("data/paper_semantic_scholar_results.csv")
CSV_COLUMNS = [
    "id",
    "conversation_id",
    "conversation_title",
    "status",
    "paper_id",
    "corpus_id",
    "matched_title",
    "url",
    "abstract",
    "year",
    "venue",
    "publication_date",
    "is_open_access",
    "match_score",
    "citation_count",
    "reference_count",
    "authors_json",
    "external_ids_json",
    "publication_types_json",
    "publication_venue_json",
    "journal_json",
    "open_access_pdf_json",
    "raw_response_json",
    "source",
    "created_at",
    "updated_at",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export PaperSemanticScholarResult rows to a CSV file for debugging."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"CSV output path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--conversation-id",
        help="Only export one conversation's Semantic Scholar result.",
    )
    parser.add_argument(
        "--status",
        choices=("matched", "not_found"),
        help="Only export rows with the given status.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only export the first N rows after filtering.",
    )
    parser.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="desc",
        help="Sort by updated_at. Default: desc",
    )
    return parser.parse_args()


def build_statement(args: argparse.Namespace):
    order_column = (
        desc(PaperSemanticScholarResult.updated_at)
        if args.order == "desc"
        else PaperSemanticScholarResult.updated_at
    )
    statement = (
        select(PaperSemanticScholarResult, Conversation.title)
        .join(
            Conversation,
            Conversation.id == PaperSemanticScholarResult.conversation_id,
            isouter=True,
        )
        .order_by(order_column)
    )

    if args.conversation_id:
        statement = statement.where(
            PaperSemanticScholarResult.conversation_id == args.conversation_id
        )

    if args.status:
        statement = statement.where(PaperSemanticScholarResult.status == args.status)

    if args.limit is not None:
        statement = statement.limit(max(0, args.limit))

    return statement


def normalize_value(value):
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def main() -> int:
    args = parse_args()
    SQLModel.metadata.create_all(engine)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with Session(engine) as session:
        rows = session.exec(build_statement(args)).all()

    with args.output.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for result, conversation_title in rows:
            writer.writerow(
                {
                    "id": normalize_value(result.id),
                    "conversation_id": normalize_value(result.conversation_id),
                    "conversation_title": normalize_value(conversation_title),
                    "status": normalize_value(result.status),
                    "paper_id": normalize_value(result.paper_id),
                    "corpus_id": normalize_value(result.corpus_id),
                    "matched_title": normalize_value(result.matched_title),
                    "url": normalize_value(result.url),
                    "abstract": normalize_value(result.abstract),
                    "year": normalize_value(result.year),
                    "venue": normalize_value(result.venue),
                    "publication_date": normalize_value(result.publication_date),
                    "is_open_access": normalize_value(result.is_open_access),
                    "match_score": normalize_value(result.match_score),
                    "citation_count": normalize_value(result.citation_count),
                    "reference_count": normalize_value(result.reference_count),
                    "authors_json": normalize_value(result.authors_json),
                    "external_ids_json": normalize_value(result.external_ids_json),
                    "publication_types_json": normalize_value(result.publication_types_json),
                    "publication_venue_json": normalize_value(result.publication_venue_json),
                    "journal_json": normalize_value(result.journal_json),
                    "open_access_pdf_json": normalize_value(result.open_access_pdf_json),
                    "raw_response_json": normalize_value(result.raw_response_json),
                    "source": normalize_value(result.source),
                    "created_at": normalize_value(result.created_at),
                    "updated_at": normalize_value(result.updated_at),
                }
            )

    print(f"Exported {len(rows)} row(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
