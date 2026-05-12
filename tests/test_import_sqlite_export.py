from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine, select

from backend.platform.models import (
    Conversation,
    FileRecord,
    Message,
    PaperFigure,
    PaperSemanticScholarResult,
    PaperTable,
)
from scripts.import_sqlite_export import import_candidates, load_sqlite_export


class ImportSqliteExportTest(unittest.TestCase):
    def setUp(self):
        self.source_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.source_file.close()
        self.addCleanup(Path(self.source_file.name).unlink, missing_ok=True)
        self.target_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.target_file.close()
        self.addCleanup(Path(self.target_file.name).unlink, missing_ok=True)
        self.source_engine = create_engine(f"sqlite:///{self.source_file.name}")
        self.target_engine = create_engine(f"sqlite:///{self.target_file.name}")
        SQLModel.metadata.create_all(self.source_engine)
        SQLModel.metadata.create_all(self.target_engine)

    def _seed_complete_source(self, conversation_id: str = "conv-1", fingerprint: str = "fp-1", title: str = "Paper") -> None:
        now = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)
        with Session(self.source_engine) as session:
            session.add(Conversation(id=conversation_id, title=title, original_filename="paper.pdf", created_at=now))
            session.add(
                FileRecord(
                    id=f"file-{conversation_id}",
                    conversation_id=conversation_id,
                    filename="paper.pdf",
                    fingerprint=fingerprint,
                    poe_url="https://example.invalid/paper.pdf",
                    content_type="text/plain",
                    poe_name="paper.pdf",
                    uploaded_at=now,
                )
            )
            session.add(
                Message(
                    conversation_id=conversation_id,
                    message_kind="bot_reply",
                    visible_to_user=True,
                    content="hello\x00 world",
                    client_payload_json='{"ok": true}',
                    created_at=now,
                )
            )
            session.add(
                Message(
                    conversation_id=conversation_id,
                    message_kind="bot_reply",
                    visible_to_user=True,
                    content="",
                    client_payload_json=None,
                    created_at=now,
                )
            )
            session.add(
                PaperFigure(
                    conversation_id=conversation_id,
                    page_number=1,
                    figure_index=1,
                    figure_label="Figure 1",
                    caption="cap",
                    image_mime_type="image/webp",
                    image_data=b"figure-bytes",
                    image_width=10,
                    image_height=20,
                    created_at=now,
                )
            )
            session.add(
                PaperTable(
                    conversation_id=conversation_id,
                    page_number=2,
                    table_index=1,
                    table_label="Table 1",
                    caption="tab",
                    image_mime_type="image/webp",
                    image_data=b"table-bytes",
                    image_width=30,
                    image_height=40,
                    created_at=now,
                )
            )
            session.add(
                PaperSemanticScholarResult(
                    conversation_id=conversation_id,
                    status="matched",
                    paper_id=f"paper-{conversation_id}",
                    corpus_id=123,
                    matched_title=title,
                    venue_abbr="CVPR",
                    ccf_category="A",
                    ccf_type="conf",
                    raw_response_json='{"paper": true}',
                    source="semantic_scholar",
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()

    def test_import_complete_conversation_preserves_counts_and_cleans_nul(self):
        self._seed_complete_source()
        candidates, skipped = load_sqlite_export(Path(self.source_file.name))
        self.assertEqual(len(candidates), 1)
        self.assertEqual(skipped, [])

        with Session(self.target_engine) as session:
            totals = import_candidates(session, candidates, replace_existing=False, write=True)

        self.assertEqual(totals.conversations, 1)
        self.assertEqual(totals.messages, 2)
        self.assertEqual(totals.figures, 1)
        self.assertEqual(totals.tables, 1)
        self.assertEqual(totals.semantic_results, 1)
        self.assertEqual(totals.nul_chars_removed, 1)
        with Session(self.target_engine) as session:
            record = session.exec(select(FileRecord).where(FileRecord.conversation_id == "conv-1")).one()
            self.assertEqual(record.fingerprint, "fp-1")
            self.assertEqual(record.content_type, "text/plain")
            messages = session.exec(select(Message).where(Message.conversation_id == "conv-1").order_by(Message.id)).all()
            self.assertEqual(messages[0].content, "hello world")
            self.assertEqual(messages[1].content, "")
            figure = session.exec(select(PaperFigure).where(PaperFigure.conversation_id == "conv-1")).one()
            self.assertEqual(figure.image_data, b"figure-bytes")

    def test_duplicate_strategy_skips_existing_target(self):
        self._seed_complete_source()
        candidates, _ = load_sqlite_export(Path(self.source_file.name))
        with Session(self.target_engine) as session:
            session.add(Conversation(id="conv-1", title="Existing"))
            session.commit()
            totals = import_candidates(session, candidates, replace_existing=False, write=True)

        self.assertEqual(totals.skipped_duplicates, 1)
        with Session(self.target_engine) as session:
            self.assertEqual(session.get(Conversation, "conv-1").title, "Existing")
            self.assertEqual(session.exec(select(Message).where(Message.conversation_id == "conv-1")).all(), [])

    def test_replace_existing_deletes_children_and_imports_new_rows(self):
        self._seed_complete_source(title="New Paper")
        self._seed_complete_source("other", "fp-other", "Other")
        candidates, _ = load_sqlite_export(Path(self.source_file.name), conversation_ids={"conv-1"})
        with Session(self.target_engine) as session:
            session.add(Conversation(id="conv-1", title="Old"))
            session.add(Message(conversation_id="conv-1", message_kind="bot_reply", visible_to_user=True, content="old"))
            session.add(Conversation(id="untouched", title="Untouched"))
            session.commit()
            totals = import_candidates(session, candidates, replace_existing=True, write=True)

        self.assertEqual(totals.replaced_conversations, 1)
        with Session(self.target_engine) as session:
            self.assertEqual(session.get(Conversation, "conv-1").title, "New Paper")
            self.assertEqual(session.get(Conversation, "untouched").title, "Untouched")
            contents = [row.content for row in session.exec(select(Message).where(Message.conversation_id == "conv-1")).all()]
            self.assertNotIn("old", contents)

    def test_incomplete_conversation_is_skipped_by_default(self):
        with Session(self.source_engine) as session:
            session.add(Conversation(id="empty", title="Empty"))
            session.commit()

        candidates, skipped = load_sqlite_export(Path(self.source_file.name))
        self.assertEqual(candidates, [])
        self.assertEqual(skipped[0]["id"], "empty")
        self.assertEqual(skipped[0]["reason"], "missing_file_record")

    def test_source_integrity_rejects_orphans(self):
        with sqlite3.connect(self.source_file.name) as connection:
            connection.execute(
                """
                INSERT INTO message (conversation_id, message_kind, section_category, visible_to_user, content, client_payload_json, created_at)
                VALUES ('missing', 'bot_reply', NULL, 1, 'orphan', NULL, '2026-05-12 10:00:00')
                """
            )
            connection.commit()
        with self.assertRaisesRegex(RuntimeError, "orphan"):
            load_sqlite_export(Path(self.source_file.name))


if __name__ == "__main__":
    unittest.main()
