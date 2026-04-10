from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fastapi_poe as fp
from pypdf import PdfWriter
from sqlmodel import SQLModel, Session, create_engine, select

from backend.modules import ingest
from backend.platform.models import Conversation, FileRecord, Message


def build_test_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        writer.write(tmp)
        tmp.flush()
        return Path(tmp.name).read_bytes()


class IngestDuplicateHandlingTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.addCleanup(Path(self.db_file.name).unlink, missing_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_file.name}")
        SQLModel.metadata.create_all(self.engine)

    def test_find_existing_file_skips_orphan_records(self):
        with Session(self.engine) as session:
            session.add(
                FileRecord(
                    id="file-orphan",
                    conversation_id="missing-conversation",
                    filename="paper.pdf",
                    fingerprint="same-fingerprint",
                    poe_url="https://example.invalid/paper.pdf",
                    content_type="application/pdf",
                    poe_name="paper.pdf",
                )
            )
            session.commit()

        with patch.object(ingest, "engine", self.engine):
            with Session(self.engine) as session:
                record = ingest.find_existing_file(session, "same-fingerprint")
                self.assertIsNone(record)

            with Session(self.engine) as session:
                remaining = session.exec(select(FileRecord).where(FileRecord.fingerprint == "same-fingerprint")).all()
                self.assertEqual(remaining, [])

    def test_handle_ingest_task_recovers_from_orphan_duplicate_record(self):
        pdf_bytes = build_test_pdf_bytes()
        staged_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        staged_pdf.write(pdf_bytes)
        staged_pdf.flush()
        staged_pdf.close()
        self.addCleanup(Path(staged_pdf.name).unlink, missing_ok=True)

        fingerprint = ingest.hashlib.sha256(pdf_bytes).hexdigest()
        with Session(self.engine) as session:
            session.add(
                FileRecord(
                    id="file-orphan",
                    conversation_id="missing-conversation",
                    filename="paper.pdf",
                    fingerprint=fingerprint,
                    poe_url="https://example.invalid/paper.pdf",
                    content_type="application/pdf",
                    poe_name="paper.pdf",
                )
            )
            session.commit()

        payload = ingest.IngestPdfTaskPayload(
            upload_path=staged_pdf.name,
            filename="paper.pdf",
            poe_model="poe-model",
            title_model="title-model",
            tag_model="tag-model",
            extract_tags=False,
            api_key="test-key",
        )
        uploaded_attachment = fp.Attachment(
            url="https://example.invalid/new-paper.pdf",
            content_type="application/pdf",
            name="paper.pdf",
        )
        first_page_attachment = fp.Attachment(
            url="https://example.invalid/first-page.pdf",
            content_type="application/pdf",
            name="first_page_paper.pdf",
        )

        with (
            patch.object(ingest, "engine", self.engine),
            patch.object(ingest, "mark_task_progress"),
            patch.object(ingest, "update_task_record"),
            patch.object(ingest, "upload_file", AsyncMock(side_effect=[uploaded_attachment, first_page_attachment])),
            patch.object(ingest, "extract_title_from_pdf", AsyncMock(return_value="Recovered Title")),
            patch.object(
                ingest,
                "get_bot_response",
                AsyncMock(
                    side_effect=[
                        '{"status":"ok","units":["ABSTRACT","1 INTRODUCTION"],"appendix_units":["APPENDIX A"],"reason":"","glossary":[{"term":"mesh face","candidates":["三角面片","网格面"]}]}',
                    ]
                ),
            ),
            patch.object(ingest, "extract_and_store_figures", return_value=[]),
            patch.object(ingest, "extract_and_store_tables", return_value=[]),
            patch.object(ingest, "refresh_conversation_semantic_result", return_value=None),
        ):
            result = asyncio.run(ingest.handle_ingest_task("task-1", payload))

        self.assertEqual(result["title"], "Recovered Title")
        self.assertNotEqual(result["conversation_id"], "missing-conversation")
        self.assertEqual(result["translation_plan"]["units"], ["ABSTRACT", "1 INTRODUCTION"])
        self.assertEqual(result["translation_status"]["current_unit_id"], "")
        self.assertEqual(result["translation_status"]["next_unit_id"], "ABSTRACT")
        self.assertEqual(result["translation_status"]["state"], "IN_PROGRESS")
        self.assertEqual(result["translation_glossary"]["status"], "draft")
        self.assertEqual(result["translation_glossary"]["entries"][0]["selected"], "三角面片")

        with Session(self.engine) as session:
            conversation = session.get(Conversation, result["conversation_id"])
            self.assertIsNotNone(conversation)
            file_records = session.exec(select(FileRecord).where(FileRecord.fingerprint == fingerprint)).all()
            self.assertEqual(len(file_records), 1)
            self.assertEqual(file_records[0].conversation_id, result["conversation_id"])
            first_bot_message = session.exec(
                select(Message)
                .where(Message.conversation_id == result["conversation_id"], Message.message_kind == "bot_reply")
                .order_by(Message.id)
            ).first()
            self.assertIsNotNone(first_bot_message)
            payload = json.loads(first_bot_message.client_payload_json or "{}")
            self.assertEqual(payload["translation_plan"]["appendix_units"], ["APPENDIX A"])
            self.assertEqual(payload["translation_status"]["current_unit_id"], "")
            self.assertEqual(payload["translation_glossary"]["status"], "draft")
            self.assertEqual(payload["translation_glossary"]["entries"][0]["term"], "mesh face")


if __name__ == "__main__":
    unittest.main()
