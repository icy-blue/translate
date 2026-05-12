#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from sqlmodel import Session, SQLModel, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.platform.config import engine
from backend.platform.models import (
    AsyncJob,
    Conversation,
    FileRecord,
    Message,
    PaperFigure,
    PaperSemanticScholarResult,
    PaperTable,
    PaperTag,
)


@dataclass(frozen=True)
class ImportCandidate:
    conversation: dict[str, Any]
    file_record: dict[str, Any]
    messages: list[dict[str, Any]]
    figures: list[dict[str, Any]]
    tables: list[dict[str, Any]]
    tags: list[dict[str, Any]]
    semantic: dict[str, Any] | None

    @property
    def conversation_id(self) -> str:
        return str(self.conversation["id"])

    @property
    def title(self) -> str:
        return str(self.conversation.get("title") or "")

    @property
    def fingerprint(self) -> str | None:
        value = self.file_record.get("fingerprint")
        return str(value) if value else None

    @property
    def paper_id(self) -> str | None:
        if not self.semantic:
            return None
        value = self.semantic.get("paper_id")
        return str(value) if value else None


@dataclass
class ImportTotals:
    conversations: int = 0
    file_records: int = 0
    messages: int = 0
    figures: int = 0
    tables: int = 0
    tags: int = 0
    semantic_results: int = 0
    skipped_duplicates: int = 0
    skipped_incomplete: int = 0
    replaced_conversations: int = 0
    nul_chars_removed: int = 0
    actions: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import translated-paper sessions from a SQLite export.")
    parser.add_argument("--source", required=True, type=Path, help="Path to source SQLite export.")
    parser.add_argument("--conversation-id", action="append", default=[], help="Only import this conversation id. Repeatable.")
    parser.add_argument(
        "--skip-incomplete",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip conversations missing file/message/semantic data. Enabled by default.",
    )
    parser.add_argument("--replace-existing", action="store_true", help="Delete matching target sessions before importing.")
    parser.add_argument("--write", action="store_true", help="Actually update the target database. Default is dry-run.")
    return parser.parse_args()


def parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        raise ValueError("Required datetime value is missing.")
    text = str(value).strip()
    if not text:
        raise ValueError("Required datetime value is empty.")
    return datetime.fromisoformat(text.replace(" ", "T"))


def clean_text(value: Any, counter: list[int] | None = None, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError("Required text value is missing.")
        return None
    text = str(value)
    if "\x00" in text:
        removed = text.count("\x00")
        text = text.replace("\x00", "")
        if counter is not None:
            counter[0] += removed
    return text


def mask_database_url(database_url: str) -> str:
    try:
        parts = urlsplit(database_url)
    except Exception:
        return "<unparseable>"
    if not parts.password:
        return database_url
    username = parts.username or ""
    hostname = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    netloc = f"{username}:***@{hostname}{port}" if username else f"***@{hostname}{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def rows(connection: sqlite3.Connection, query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    cursor = connection.execute(query, tuple(params))
    return [dict(row) for row in cursor.fetchall()]


def one_row(connection: sqlite3.Connection, query: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    result = rows(connection, query, params)
    return result[0] if result else None


def assert_source_ok(connection: sqlite3.Connection) -> None:
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise RuntimeError(f"Source integrity_check failed: {integrity}")
    for table in ("message", "filerecord", "paperfigure", "papertable", "papertag", "papersemanticscholarresult"):
        count = connection.execute(
            f"""
            SELECT count(*)
            FROM {table} child
            LEFT JOIN conversation c ON c.id = child.conversation_id
            WHERE c.id IS NULL
            """
        ).fetchone()[0]
        if count:
            raise RuntimeError(f"Source has {count} orphan rows in {table}.")


def load_candidates(
    connection: sqlite3.Connection,
    *,
    conversation_ids: set[str] | None = None,
    skip_incomplete: bool = True,
) -> tuple[list[ImportCandidate], list[dict[str, Any]]]:
    conversations = rows(connection, "SELECT * FROM conversation ORDER BY created_at, id")
    candidates: list[ImportCandidate] = []
    skipped: list[dict[str, Any]] = []
    for conversation in conversations:
        conversation_id = str(conversation["id"])
        if conversation_ids and conversation_id not in conversation_ids:
            continue
        file_record = one_row(connection, "SELECT * FROM filerecord WHERE conversation_id = ?", (conversation_id,))
        messages = rows(connection, "SELECT * FROM message WHERE conversation_id = ? ORDER BY id", (conversation_id,))
        figures = rows(connection, "SELECT * FROM paperfigure WHERE conversation_id = ? ORDER BY id", (conversation_id,))
        tables = rows(connection, "SELECT * FROM papertable WHERE conversation_id = ? ORDER BY id", (conversation_id,))
        tags = rows(connection, "SELECT * FROM papertag WHERE conversation_id = ? ORDER BY id", (conversation_id,))
        semantic = one_row(connection, "SELECT * FROM papersemanticscholarresult WHERE conversation_id = ?", (conversation_id,))
        incomplete_reason = None
        if not file_record:
            incomplete_reason = "missing_file_record"
        elif not messages:
            incomplete_reason = "missing_messages"
        elif not semantic:
            incomplete_reason = "missing_semantic"
        if incomplete_reason:
            payload = {
                "id": conversation_id,
                "title": conversation.get("title") or "",
                "reason": incomplete_reason,
                "messages": len(messages),
                "figures": len(figures),
                "tables": len(tables),
                "tags": len(tags),
                "has_file": bool(file_record),
                "has_semantic": bool(semantic),
            }
            if skip_incomplete:
                skipped.append(payload)
                continue
            raise RuntimeError(f"Conversation {conversation_id} is incomplete: {incomplete_reason}")
        candidates.append(ImportCandidate(conversation, file_record, messages, figures, tables, tags, semantic))
    return candidates, skipped


def find_duplicate_reasons(session: Session, candidate: ImportCandidate) -> list[str]:
    reasons: list[str] = []
    if session.get(Conversation, candidate.conversation_id):
        reasons.append("conversation_id")
    if candidate.fingerprint:
        match = session.exec(select(FileRecord).where(FileRecord.fingerprint == candidate.fingerprint)).first()
        if match:
            reasons.append("fingerprint")
    if candidate.paper_id:
        match = session.exec(select(PaperSemanticScholarResult).where(PaperSemanticScholarResult.paper_id == candidate.paper_id)).first()
        if match:
            reasons.append("paper_id")
    return reasons


def delete_conversations(session: Session, conversation_ids: list[str]) -> int:
    if not conversation_ids:
        return 0
    for model in (Message, FileRecord, PaperFigure, PaperTable, PaperTag, PaperSemanticScholarResult, AsyncJob):
        for row in session.exec(select(model).where(model.conversation_id.in_(conversation_ids))).all():
            session.delete(row)
    conversations = session.exec(select(Conversation).where(Conversation.id.in_(conversation_ids))).all()
    for conversation in conversations:
        session.delete(conversation)
    return len(conversations)


def insert_candidate(session: Session, candidate: ImportCandidate, nul_counter: list[int] | None = None) -> dict[str, int]:
    c = candidate.conversation
    session.add(
        Conversation(
            id=clean_text(c["id"], nul_counter, required=True),
            title=clean_text(c.get("title"), nul_counter),
            original_filename=clean_text(c.get("original_filename"), nul_counter),
            status=clean_text(c.get("status"), nul_counter) or "active",
            created_at=parse_dt(c.get("created_at")),
        )
    )

    f = candidate.file_record
    session.add(
        FileRecord(
            id=clean_text(f["id"], nul_counter, required=True),
            conversation_id=clean_text(f["conversation_id"], nul_counter, required=True),
            filename=clean_text(f["filename"], nul_counter, required=True),
            fingerprint=clean_text(f.get("fingerprint"), nul_counter),
            poe_url=clean_text(f["poe_url"], nul_counter, required=True),
            content_type=clean_text(f["content_type"], nul_counter, required=True),
            poe_name=clean_text(f["poe_name"], nul_counter, required=True),
            uploaded_at=parse_dt(f.get("uploaded_at")),
        )
    )

    for m in candidate.messages:
        session.add(
            Message(
                conversation_id=clean_text(m["conversation_id"], nul_counter, required=True),
                message_kind=clean_text(m["message_kind"], nul_counter, required=True),
                section_category=clean_text(m.get("section_category"), nul_counter),
                visible_to_user=bool(m["visible_to_user"]),
                content=clean_text(m["content"], nul_counter, required=True),
                client_payload_json=clean_text(m.get("client_payload_json"), nul_counter),
                created_at=parse_dt(m.get("created_at")),
            )
        )

    for fig in candidate.figures:
        session.add(
            PaperFigure(
                conversation_id=clean_text(fig["conversation_id"], nul_counter, required=True),
                page_number=fig["page_number"],
                figure_index=fig["figure_index"],
                figure_label=clean_text(fig.get("figure_label"), nul_counter),
                caption=clean_text(fig["caption"], nul_counter, required=True),
                image_mime_type=clean_text(fig.get("image_mime_type"), nul_counter),
                image_data=fig.get("image_data"),
                image_width=fig["image_width"],
                image_height=fig["image_height"],
                created_at=parse_dt(fig.get("created_at")),
            )
        )

    for table in candidate.tables:
        session.add(
            PaperTable(
                conversation_id=clean_text(table["conversation_id"], nul_counter, required=True),
                page_number=table["page_number"],
                table_index=table["table_index"],
                table_label=clean_text(table.get("table_label"), nul_counter),
                caption=clean_text(table["caption"], nul_counter, required=True),
                image_mime_type=clean_text(table.get("image_mime_type"), nul_counter),
                image_data=table.get("image_data"),
                image_width=table["image_width"],
                image_height=table["image_height"],
                created_at=parse_dt(table.get("created_at")),
            )
        )

    for tag in candidate.tags:
        session.add(
            PaperTag(
                conversation_id=clean_text(tag["conversation_id"], nul_counter, required=True),
                category_code=clean_text(tag["category_code"], nul_counter, required=True),
                category_label=clean_text(tag["category_label"], nul_counter, required=True),
                tag_code=clean_text(tag["tag_code"], nul_counter, required=True),
                tag_label=clean_text(tag["tag_label"], nul_counter, required=True),
                tag_path=clean_text(tag["tag_path"], nul_counter, required=True),
                source=clean_text(tag.get("source"), nul_counter) or "poe",
                created_at=parse_dt(tag.get("created_at")),
            )
        )

    s = candidate.semantic
    if s:
        session.add(
            PaperSemanticScholarResult(
                conversation_id=clean_text(s["conversation_id"], nul_counter, required=True),
                status=clean_text(s["status"], nul_counter, required=True),
                paper_id=clean_text(s.get("paper_id"), nul_counter),
                corpus_id=s.get("corpus_id"),
                matched_title=clean_text(s.get("matched_title"), nul_counter),
                url=clean_text(s.get("url"), nul_counter),
                abstract=clean_text(s.get("abstract"), nul_counter),
                year=s.get("year"),
                venue=clean_text(s.get("venue"), nul_counter),
                venue_abbr=clean_text(s.get("venue_abbr"), nul_counter) or "",
                ccf_category=clean_text(s.get("ccf_category"), nul_counter) or "None",
                ccf_type=clean_text(s.get("ccf_type"), nul_counter) or "None",
                publication_date=clean_text(s.get("publication_date"), nul_counter),
                is_open_access=s.get("is_open_access"),
                match_score=s.get("match_score"),
                citation_count=s.get("citation_count"),
                reference_count=s.get("reference_count"),
                authors_json=clean_text(s.get("authors_json"), nul_counter),
                external_ids_json=clean_text(s.get("external_ids_json"), nul_counter),
                publication_types_json=clean_text(s.get("publication_types_json"), nul_counter),
                publication_venue_json=clean_text(s.get("publication_venue_json"), nul_counter),
                journal_json=clean_text(s.get("journal_json"), nul_counter),
                open_access_pdf_json=clean_text(s.get("open_access_pdf_json"), nul_counter),
                raw_response_json=clean_text(s.get("raw_response_json"), nul_counter) or "{}",
                source=clean_text(s.get("source"), nul_counter) or "semantic_scholar",
                created_at=parse_dt(s.get("created_at")),
                updated_at=parse_dt(s.get("updated_at")),
            )
        )

    return {
        "conversations": 1,
        "file_records": 1,
        "messages": len(candidate.messages),
        "figures": len(candidate.figures),
        "tables": len(candidate.tables),
        "tags": len(candidate.tags),
        "semantic_results": 1 if candidate.semantic else 0,
    }


def import_candidates(
    session: Session,
    candidates: list[ImportCandidate],
    *,
    replace_existing: bool,
    write: bool,
) -> ImportTotals:
    totals = ImportTotals()
    if replace_existing:
        ids = [candidate.conversation_id for candidate in candidates]
        totals.replaced_conversations = delete_conversations(session, ids)
        totals.actions.append(f"REPLACE candidates={len(ids)} existing={totals.replaced_conversations}")
        if write:
            session.commit()
        else:
            session.rollback()

    for candidate in candidates:
        duplicate_reasons = [] if replace_existing else find_duplicate_reasons(session, candidate)
        if duplicate_reasons:
            totals.skipped_duplicates += 1
            totals.actions.append(f"SKIP duplicate {candidate.conversation_id}: {','.join(duplicate_reasons)}")
            continue
        if not write:
            totals.actions.append(
                f"WOULD IMPORT {candidate.conversation_id}: messages={len(candidate.messages)} "
                f"figures={len(candidate.figures)} tables={len(candidate.tables)} tags={len(candidate.tags)} "
                f"semantic={1 if candidate.semantic else 0}"
            )
            continue
        nul_counter = [0]
        counts = insert_candidate(session, candidate, nul_counter)
        session.commit()
        totals.nul_chars_removed += nul_counter[0]
        for key, value in counts.items():
            setattr(totals, key, getattr(totals, key) + value)
        totals.actions.append(
            f"IMPORTED {candidate.conversation_id}: messages={counts['messages']} figures={counts['figures']} "
            f"tables={counts['tables']} tags={counts['tags']} semantic={counts['semantic_results']} "
            f"nul_removed={nul_counter[0]}"
        )
    return totals


def load_sqlite_export(
    source: Path,
    *,
    conversation_ids: set[str] | None = None,
    skip_incomplete: bool = True,
) -> tuple[list[ImportCandidate], list[dict[str, Any]]]:
    if not source.exists():
        raise RuntimeError(f"Source database does not exist: {source}")
    with sqlite3.connect(source) as connection:
        connection.row_factory = sqlite3.Row
        assert_source_ok(connection)
        return load_candidates(connection, conversation_ids=conversation_ids, skip_incomplete=skip_incomplete)


def main() -> int:
    args = parse_args()
    candidates, skipped = load_sqlite_export(
        args.source,
        conversation_ids=set(args.conversation_id) if args.conversation_id else None,
        skip_incomplete=args.skip_incomplete,
    )
    SQLModel.metadata.create_all(engine)
    print(f"Target DATABASE_URL: {mask_database_url(str(engine.url))}")
    print(f"Source candidates: {len(candidates)} complete conversation(s)")
    if skipped:
        print(f"Skipped incomplete: {len(skipped)}")
        for item in skipped:
            print(f"  - {item['id']} {item['reason']} messages={item['messages']} semantic={item['has_semantic']}")
    for candidate in candidates:
        print(
            f"  - {candidate.conversation_id} | {candidate.title} | messages={len(candidate.messages)} "
            f"figures={len(candidate.figures)} tables={len(candidate.tables)} tags={len(candidate.tags)} "
            f"paper_id={candidate.paper_id or '-'}"
        )

    mode = "WRITE" if args.write else "DRY-RUN"
    print(f"Mode: {mode}")
    with Session(engine) as session:
        totals = import_candidates(session, candidates, replace_existing=args.replace_existing, write=args.write)
        totals.skipped_incomplete = len(skipped)

    for action in totals.actions:
        print(action)
    print("Final summary:")
    for key in (
        "conversations",
        "file_records",
        "messages",
        "figures",
        "tables",
        "tags",
        "semantic_results",
        "skipped_duplicates",
        "skipped_incomplete",
        "replaced_conversations",
        "nul_chars_removed",
    ):
        print(f"  {key}: {getattr(totals, key)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
