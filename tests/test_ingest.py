from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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
        self.files_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.files_dir.cleanup)
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
            patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)),
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
            self.assertEqual(file_records[0].poe_url, f"/files/{result['conversation_id']}/{file_records[0].id}.pdf")
            self.assertTrue((Path(self.files_dir.name) / result["conversation_id"] / f"{file_records[0].id}.pdf").is_file())
            self.assertEqual(result["pdf_url"], file_records[0].poe_url)
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

    def test_duplicate_upload_restores_missing_local_pdf_url(self):
        pdf_bytes = build_test_pdf_bytes()
        staged_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        staged_pdf.write(pdf_bytes)
        staged_pdf.flush()
        staged_pdf.close()
        self.addCleanup(Path(staged_pdf.name).unlink, missing_ok=True)

        fingerprint = ingest.hashlib.sha256(pdf_bytes).hexdigest()
        with Session(self.engine) as session:
            session.add(Conversation(id="conv-existing", title="Paper", original_filename="paper.pdf"))
            session.add(
                FileRecord(
                    id="file-existing",
                    conversation_id="conv-existing",
                    filename="paper.pdf",
                    fingerprint=fingerprint,
                    poe_url="",
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

        with (
            patch.object(ingest, "engine", self.engine),
            patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)),
            patch.object(ingest, "mark_task_progress"),
            patch.object(ingest, "update_task_record"),
        ):
            result = asyncio.run(ingest.handle_ingest_task("task-duplicate", payload))

        self.assertTrue(result["exists"])
        self.assertEqual(result["conversation_id"], "conv-existing")
        self.assertEqual(result["pdf_url"], "/files/conv-existing/file-existing.pdf")
        self.assertEqual((Path(self.files_dir.name) / "conv-existing" / "file-existing.pdf").read_bytes(), pdf_bytes)
        with Session(self.engine) as session:
            self.assertEqual(session.get(FileRecord, "file-existing").poe_url, "/files/conv-existing/file-existing.pdf")

    def test_handle_ingest_task_extracts_tags_with_semantic_abstract_fallback(self):
        pdf_bytes = build_test_pdf_bytes()
        staged_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        staged_pdf.write(pdf_bytes)
        staged_pdf.flush()
        staged_pdf.close()
        self.addCleanup(Path(staged_pdf.name).unlink, missing_ok=True)

        payload = ingest.IngestPdfTaskPayload(
            upload_path=staged_pdf.name,
            filename="paper.pdf",
            poe_model="poe-model",
            title_model="title-model",
            tag_model="tag-model",
            extract_tags=True,
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
        semantic_result = SimpleNamespace(
            abstract="This paper proposes a point cloud registration method.",
            venue_abbr="",
            ccf_category="None",
            ccf_type="None",
            citation_count=None,
            venue=None,
            year=None,
            updated_at=datetime.now(timezone.utc),
        )

        with (
            patch.object(ingest, "engine", self.engine),
            patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)),
            patch.object(ingest, "mark_task_progress"),
            patch.object(ingest, "update_task_record"),
            patch.object(ingest, "upload_file", AsyncMock(side_effect=[uploaded_attachment, first_page_attachment])),
            patch.object(ingest, "extract_title_from_pdf", AsyncMock(return_value="Recovered Title")),
            patch.object(
                ingest,
                "get_bot_response",
                AsyncMock(
                    return_value='{"status":"ok","units":["ABSTRACT"],"appendix_units":[],"reason":"","glossary":[]}'
                ),
            ),
            patch.object(ingest, "extract_and_store_figures", return_value=[]),
            patch.object(ingest, "extract_and_store_tables", return_value=[]),
            patch.object(ingest, "refresh_conversation_semantic_result", return_value=semantic_result),
            patch.object(ingest, "extract_and_store_tags", AsyncMock(return_value=[])) as extract_tags_mock,
        ):
            asyncio.run(ingest.handle_ingest_task("task-2", payload))

        extract_tags_mock.assert_awaited_once()
        self.assertEqual(extract_tags_mock.await_args.args[3], "")
        self.assertEqual(
            extract_tags_mock.await_args.kwargs["fallback_abstract"],
            "This paper proposes a point cloud registration method.",
        )

    def test_extract_pdf_abstract_for_tagging_reads_abstract_section(self):
        pdf_bytes = build_test_pdf_bytes()
        with patch.object(
            ingest.PdfReader,
            "__init__",
            return_value=None,
        ), patch.object(
            ingest.PdfReader,
            "pages",
            [
                SimpleNamespace(
                    extract_text=lambda: (
                        "Paper Title\n\n"
                        "Abstract\n"
                        "We propose a controllable 3D generation method with differentiable rendering.\n"
                        "1 Introduction\n"
                        "This part should not be included."
                    )
                )
            ],
        ):
            abstract = ingest.extract_pdf_abstract_for_tagging(pdf_bytes)

        self.assertEqual(
            abstract,
            "We propose a controllable 3D generation method with differentiable rendering.",
        )

    def test_handle_ingest_task_passes_deepseek_provider_to_model_calls(self):
        pdf_bytes = build_test_pdf_bytes()
        staged_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        staged_pdf.write(pdf_bytes)
        staged_pdf.flush()
        staged_pdf.close()
        self.addCleanup(Path(staged_pdf.name).unlink, missing_ok=True)

        payload = ingest.IngestPdfTaskPayload(
            upload_path=staged_pdf.name,
            filename="paper.pdf",
            provider="deepseek",
            poe_model="deepseek-v4-pro",
            title_model="deepseek-v4-pro",
            tag_model="deepseek-v4-flash",
            extract_tags=True,
            api_key="deepseek-key",
        )
        first_page_attachment = fp.Attachment(
            url="data:application/pdf;base64,JVBERg==",
            content_type="application/pdf",
            name="first_page_paper.pdf",
        )

        with (
            patch.object(ingest, "engine", self.engine),
            patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)),
            patch.object(ingest, "mark_task_progress"),
            patch.object(ingest, "update_task_record"),
            patch.object(ingest, "upload_file", AsyncMock(return_value=first_page_attachment)),
            patch.object(ingest, "extract_title_from_pdf", AsyncMock(return_value="DeepSeek Title")) as title_mock,
            patch.object(
                ingest,
                "get_bot_response",
                AsyncMock(return_value='{"status":"ok","units":["ABSTRACT"],"appendix_units":[],"reason":"","glossary":[]}'),
            ) as response_mock,
            patch.object(ingest, "extract_and_store_figures", return_value=[]),
            patch.object(ingest, "extract_and_store_tables", return_value=[]),
            patch.object(ingest, "refresh_conversation_semantic_result", return_value=None),
            patch.object(ingest, "extract_and_store_tags", AsyncMock(return_value=[])) as extract_tags_mock,
        ):
            result = asyncio.run(ingest.handle_ingest_task("task-deepseek", payload))

        self.assertEqual(result["title"], "DeepSeek Title")
        self.assertEqual(title_mock.await_args.kwargs["provider"], "deepseek")
        self.assertEqual(response_mock.await_args.kwargs["provider"], "deepseek")
        self.assertEqual(extract_tags_mock.await_args.kwargs["provider"], "deepseek")

    def test_deepseek_ingest_does_not_pass_semantic_arxiv_id_to_planner(self):
        pdf_bytes = build_test_pdf_bytes()
        staged_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        staged_pdf.write(pdf_bytes)
        staged_pdf.flush()
        staged_pdf.close()
        self.addCleanup(Path(staged_pdf.name).unlink, missing_ok=True)

        payload = ingest.IngestPdfTaskPayload(
            upload_path=staged_pdf.name,
            filename="paper.pdf",
            provider="deepseek",
            poe_model="deepseek-v4-pro",
            title_model="deepseek-v4-pro",
            tag_model="deepseek-v4-flash",
            extract_tags=False,
            api_key="deepseek-key",
        )
        semantic_result = SimpleNamespace(
            external_ids_json='{"ArXiv": "2605.10922v1"}',
            open_access_pdf_json=None,
            url=None,
            paper_id="paper-id",
            matched_title="Semantic arXiv Paper",
            abstract="",
            venue_abbr="",
            ccf_category="None",
            ccf_type="None",
            citation_count=None,
            venue=None,
            year=None,
            updated_at=datetime.now(timezone.utc),
        )

        with (
            patch.object(ingest, "engine", self.engine),
            patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)),
            patch.object(ingest, "mark_task_progress"),
            patch.object(ingest, "update_task_record"),
            patch.object(ingest, "upload_file", AsyncMock(return_value=fp.Attachment(url="data:application/pdf;base64,JVBERg==", content_type="application/pdf", name="first_page_paper.pdf"))),
            patch.object(ingest, "extract_title_from_pdf", AsyncMock(return_value="DeepSeek Title")),
            patch.object(
                ingest,
                "get_bot_response",
                AsyncMock(return_value='{"status":"ok","units":["ABSTRACT"],"appendix_units":[],"reason":"","glossary":[]}'),
            ) as response_mock,
            patch.object(ingest, "extract_and_store_figures", return_value=[]),
            patch.object(ingest, "extract_and_store_tables", return_value=[]),
            patch.object(ingest, "refresh_conversation_semantic_result", return_value=semantic_result),
        ):
            asyncio.run(ingest.handle_ingest_task("task-deepseek-arxiv", payload))

        self.assertNotIn("arxiv_id", response_mock.await_args.kwargs)

    def test_mixed_ingest_uses_deepseek_for_title_planner_and_tags(self):
        pdf_bytes = build_test_pdf_bytes()
        staged_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        staged_pdf.write(pdf_bytes)
        staged_pdf.flush()
        staged_pdf.close()
        self.addCleanup(Path(staged_pdf.name).unlink, missing_ok=True)

        payload = ingest.IngestPdfTaskPayload(
            upload_path=staged_pdf.name,
            filename="paper.pdf",
            provider="mixed",
            poe_model="deepseek-v4-pro",
            title_model="deepseek-v4-pro",
            tag_model="deepseek-v4-flash",
            extract_tags=True,
            poe_api_key="poe-key",
            deepseek_api_key="deepseek-key",
        )
        first_page_attachment = fp.Attachment(
            url="data:application/pdf;base64,JVBERg==",
            content_type="application/pdf",
            name="first_page_paper.pdf",
        )

        with (
            patch.object(ingest, "engine", self.engine),
            patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)),
            patch.object(ingest, "mark_task_progress"),
            patch.object(ingest, "update_task_record"),
            patch.object(ingest, "upload_file", AsyncMock(return_value=first_page_attachment)),
            patch.object(ingest, "extract_title_from_pdf", AsyncMock(return_value="Mixed Title")) as title_mock,
            patch.object(
                ingest,
                "get_bot_response",
                AsyncMock(return_value='{"status":"ok","units":["ABSTRACT"],"appendix_units":[],"reason":"","glossary":[]}'),
            ) as response_mock,
            patch.object(ingest, "extract_and_store_figures", return_value=[]),
            patch.object(ingest, "extract_and_store_tables", return_value=[]),
            patch.object(ingest, "refresh_conversation_semantic_result", return_value=None),
            patch.object(ingest, "extract_and_store_tags", AsyncMock(return_value=[])) as extract_tags_mock,
        ):
            result = asyncio.run(ingest.handle_ingest_task("task-mixed", payload))

        self.assertEqual(result["title"], "Mixed Title")
        self.assertEqual(title_mock.await_args.args[1], "deepseek-key")
        self.assertEqual(title_mock.await_args.kwargs["provider"], "deepseek")
        self.assertEqual(response_mock.await_args.args[2], "deepseek-key")
        self.assertEqual(response_mock.await_args.kwargs["provider"], "deepseek")
        self.assertEqual(extract_tags_mock.await_args.args[5], "deepseek-key")
        self.assertEqual(extract_tags_mock.await_args.kwargs["provider"], "deepseek")

    def test_mixed_ingest_does_not_pass_semantic_arxiv_id_to_planner(self):
        pdf_bytes = build_test_pdf_bytes()
        staged_pdf = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        staged_pdf.write(pdf_bytes)
        staged_pdf.flush()
        staged_pdf.close()
        self.addCleanup(Path(staged_pdf.name).unlink, missing_ok=True)

        payload = ingest.IngestPdfTaskPayload(
            upload_path=staged_pdf.name,
            filename="paper.pdf",
            provider="mixed",
            poe_model="deepseek-v4-pro",
            title_model="deepseek-v4-pro",
            tag_model="deepseek-v4-flash",
            extract_tags=False,
            poe_api_key="poe-key",
            deepseek_api_key="deepseek-key",
        )
        semantic_result = SimpleNamespace(
            external_ids_json='{"ArXiv": "2605.10922v1"}',
            open_access_pdf_json=None,
            url=None,
            paper_id="paper-id",
            matched_title="Semantic arXiv Paper",
            abstract="",
            venue_abbr="",
            ccf_category="None",
            ccf_type="None",
            citation_count=None,
            venue=None,
            year=None,
            updated_at=datetime.now(timezone.utc),
        )

        with (
            patch.object(ingest, "engine", self.engine),
            patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(self.files_dir.name)),
            patch.object(ingest, "mark_task_progress"),
            patch.object(ingest, "update_task_record"),
            patch.object(ingest, "upload_file", AsyncMock(return_value=fp.Attachment(url="data:application/pdf;base64,JVBERg==", content_type="application/pdf", name="first_page_paper.pdf"))),
            patch.object(ingest, "extract_title_from_pdf", AsyncMock(return_value="Mixed Title")),
            patch.object(
                ingest,
                "get_bot_response",
                AsyncMock(return_value='{"status":"ok","units":["ABSTRACT"],"appendix_units":[],"reason":"","glossary":[]}'),
            ) as response_mock,
            patch.object(ingest, "extract_and_store_figures", return_value=[]),
            patch.object(ingest, "extract_and_store_tables", return_value=[]),
            patch.object(ingest, "refresh_conversation_semantic_result", return_value=semantic_result),
        ):
            asyncio.run(ingest.handle_ingest_task("task-mixed-arxiv", payload))

        self.assertEqual(response_mock.await_args.kwargs["provider"], "deepseek")
        self.assertNotIn("arxiv_id", response_mock.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
