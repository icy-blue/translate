from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine, func, select

from backend.domain.message_payloads import build_translation_status_payload, normalize_translation_plan_payload
from backend.modules.conversations import (
    DeleteConversationRequest,
    add_message,
    build_conversation_list_item,
    delete_conversation_data,
    delete_conversation_route,
    has_active_jobs,
)
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


class ConversationSummarySelectionTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.addCleanup(Path(self.db_file.name).unlink, missing_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_file.name}")
        SQLModel.metadata.create_all(self.engine)

    def test_list_item_summary_prefers_first_translated_bot_message(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT", "1 INTRODUCTION"],
                "appendix_units": [],
                "reason": "",
            }
        )
        planning_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[],
            current_unit_id="",
            attempted_scope="body",
            raw_translation_result=None,
        )
        abstract_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["ABSTRACT"],
            current_unit_id="ABSTRACT",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "ABSTRACT", "state": "OK", "reason": ""},
        )

        with Session(self.engine) as session:
            conversation = Conversation(id="conv-1", title="Paper", original_filename="paper.pdf")
            session.add(conversation)
            session.commit()

            add_message(
                session,
                conversation_id="conv-1",
                content="已生成全文规划，请先确认关键术语与译法。",
                message_kind="bot_reply",
                visible_to_user=True,
                client_payload={
                    "translation_plan": translation_plan,
                    "translation_status": planning_status,
                },
            )
            add_message(
                session,
                conversation_id="conv-1",
                content="# 摘要\n这是摘要译文。",
                message_kind="bot_reply",
                visible_to_user=True,
                client_payload={
                    "translation_plan": translation_plan,
                    "translation_status": abstract_status,
                },
            )
            session.commit()

            item = build_conversation_list_item(session, conversation, semantic_result=None)

        self.assertIn("这是摘要译文。", item.summary)
        self.assertNotIn("请先确认关键术语与译法", item.summary)


class ConversationDeletionTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.files_dir = tempfile.TemporaryDirectory()
        self.addCleanup(Path(self.db_file.name).unlink, missing_ok=True)
        self.addCleanup(self.files_dir.cleanup)
        self.engine = create_engine(f"sqlite:///{self.db_file.name}")
        SQLModel.metadata.create_all(self.engine)

    def _count(self, session: Session, model, conversation_id: str) -> int:
        return session.exec(select(func.count()).select_from(model).where(model.conversation_id == conversation_id)).one()

    def _seed_conversation(self, session: Session, conversation_id: str = "conv-1", job_status: str = "succeeded") -> None:
        session.add(Conversation(id=conversation_id, title="Paper", original_filename="paper.pdf"))
        session.add(
            FileRecord(
                id=f"file-{conversation_id}",
                conversation_id=conversation_id,
                filename="paper.pdf",
                poe_url=f"/files/{conversation_id}/file-{conversation_id}.pdf",
                content_type="application/pdf",
                poe_name="paper.pdf",
            )
        )
        session.add(Message(conversation_id=conversation_id, message_kind="bot_reply", visible_to_user=True, content="content"))
        session.add(
            PaperFigure(
                conversation_id=conversation_id,
                page_number=1,
                figure_index=1,
                caption="Figure",
                image_width=100,
                image_height=100,
            )
        )
        session.add(
            PaperTable(
                conversation_id=conversation_id,
                page_number=1,
                table_index=1,
                caption="Table",
                image_width=100,
                image_height=100,
            )
        )
        session.add(
            PaperTag(
                conversation_id=conversation_id,
                category_code="T",
                category_label="Task",
                tag_code="T1",
                tag_label="Task Tag",
                tag_path="Task / Task Tag",
            )
        )
        session.add(
            PaperSemanticScholarResult(
                conversation_id=conversation_id,
                status="matched",
                paper_id="paper-1",
                raw_response_json="{}",
            )
        )
        session.add(
            AsyncJob(
                id=f"job-{conversation_id}",
                job_type="ingest-pdf",
                status=job_status,
                payload_json="{}",
                conversation_id=conversation_id,
            )
        )
        session.commit()

        target_dir = Path(self.files_dir.name) / conversation_id
        target_dir.mkdir(parents=True)
        (target_dir / f"file-{conversation_id}.pdf").write_bytes(b"%PDF")

    def test_delete_conversation_data_removes_related_rows_and_files(self):
        with Session(self.engine) as session:
            self._seed_conversation(session)

            response = delete_conversation_data(session, "conv-1", Path(self.files_dir.name))
            session.commit()

            self.assertTrue(response.deleted)
            self.assertTrue(response.files_deleted)
            self.assertEqual(response.counts["Conversation"], 1)
            self.assertIsNone(session.get(Conversation, "conv-1"))
            for model in (Message, FileRecord, PaperFigure, PaperTable, PaperTag, PaperSemanticScholarResult, AsyncJob):
                self.assertEqual(self._count(session, model, "conv-1"), 0)
            self.assertFalse((Path(self.files_dir.name) / "conv-1").exists())

    def test_has_active_jobs_detects_queued_and_running_jobs(self):
        with Session(self.engine) as session:
            self._seed_conversation(session, conversation_id="queued-conv", job_status="queued")
            self._seed_conversation(session, conversation_id="running-conv", job_status="running")
            self._seed_conversation(session, conversation_id="done-conv", job_status="succeeded")

            self.assertTrue(has_active_jobs(session, "queued-conv"))
            self.assertTrue(has_active_jobs(session, "running-conv"))
            self.assertFalse(has_active_jobs(session, "done-conv"))

    def test_delete_route_rejects_wrong_confirmation_without_deleting(self):
        with Session(self.engine) as session:
            self._seed_conversation(session)

            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    delete_conversation_route(
                        "conv-1",
                        DeleteConversationRequest(confirmation_id="wrong-id"),
                        session,
                        None,
                    )
                )

            self.assertEqual(context.exception.status_code, 400)
            self.assertIsNotNone(session.get(Conversation, "conv-1"))

    def test_delete_route_returns_404_for_missing_conversation(self):
        with Session(self.engine) as session:
            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    delete_conversation_route(
                        "missing-conv",
                        DeleteConversationRequest(confirmation_id="missing-conv"),
                        session,
                        None,
                    )
                )

            self.assertEqual(context.exception.status_code, 404)

    def test_delete_route_rejects_active_jobs_without_deleting(self):
        with Session(self.engine) as session:
            self._seed_conversation(session, job_status="running")

            with self.assertRaises(HTTPException) as context:
                asyncio.run(
                    delete_conversation_route(
                        "conv-1",
                        DeleteConversationRequest(confirmation_id="conv-1"),
                        session,
                        None,
                    )
                )

            self.assertEqual(context.exception.status_code, 409)
            self.assertIsNotNone(session.get(Conversation, "conv-1"))


if __name__ == "__main__":
    unittest.main()
