from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlmodel import SQLModel, Session, create_engine

from backend.domain.message_payloads import build_translation_status_payload, normalize_translation_plan_payload
from backend.modules import translation
from backend.modules.conversations import add_message
from backend.platform.models import Conversation, FileRecord


class ContinueTranslationFlowTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.addCleanup(Path(self.db_file.name).unlink, missing_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_file.name}")
        SQLModel.metadata.create_all(self.engine)

    def _seed_conversation(self, translation_plan: dict, translation_status: dict) -> None:
        with Session(self.engine) as session:
            session.add(Conversation(id="conv-1", title="Paper", original_filename="paper.pdf"))
            session.add(
                FileRecord(
                    id="file-1",
                    conversation_id="conv-1",
                    filename="paper.pdf",
                    fingerprint="fp-1",
                    poe_url="https://example.invalid/paper.pdf",
                    content_type="application/pdf",
                    poe_name="paper.pdf",
                )
            )
            add_message(
                session,
                conversation_id="conv-1",
                content="# 摘要\n译文内容",
                message_kind="bot_reply",
                visible_to_user=True,
                client_payload={"translation_plan": translation_plan, "translation_status": translation_status},
            )
            session.commit()

    def test_continue_translation_advances_body_unit_and_moves_to_appendix(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT", "1 INTRODUCTION"],
                "appendix_units": ["APPENDIX A"],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["ABSTRACT"],
            current_unit_id="ABSTRACT",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "ABSTRACT", "state": "OK", "reason": ""},
        )
        self._seed_conversation(translation_plan, translation_status)

        payload = translation.ContinueTranslationTaskPayload(
            conversation_id="conv-1",
            action="continue",
            target_scope="body",
            poe_model="poe-model",
            api_key="test-key",
        )
        with (
            patch.object(translation, "engine", self.engine),
            patch.object(translation, "mark_task_progress"),
            patch.object(
                translation,
                "get_bot_response",
                AsyncMock(
                    return_value='[TRANSLATION_STATUS_JSON]\n{"current_unit_id":"1 INTRODUCTION","state":"OK","reason":""}\n[/TRANSLATION_STATUS_JSON]\n\n# 1 INTRODUCTION\n译文'
                ),
            ),
        ):
            result = asyncio.run(translation.handle_continue_translation("task-1", payload))

        self.assertEqual(result["translation_status"]["current_unit_id"], "1 INTRODUCTION")
        self.assertEqual(result["translation_status"]["state"], "BODY_DONE")
        self.assertEqual(result["translation_status"]["active_scope"], "appendix")
        self.assertEqual(result["translation_status"]["next_unit_id"], "APPENDIX A")

    def test_continue_translation_completes_appendix_scope(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT", "1 INTRODUCTION"],
                "appendix_units": ["APPENDIX A"],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["ABSTRACT", "1 INTRODUCTION"],
            current_unit_id="1 INTRODUCTION",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "1 INTRODUCTION", "state": "OK", "reason": ""},
        )
        self._seed_conversation(translation_plan, translation_status)

        payload = translation.ContinueTranslationTaskPayload(
            conversation_id="conv-1",
            action="continue",
            target_scope="appendix",
            poe_model="poe-model",
            api_key="test-key",
        )
        with (
            patch.object(translation, "engine", self.engine),
            patch.object(translation, "mark_task_progress"),
            patch.object(
                translation,
                "get_bot_response",
                AsyncMock(
                    return_value='[TRANSLATION_STATUS_JSON]\n{"current_unit_id":"APPENDIX A","state":"OK","reason":""}\n[/TRANSLATION_STATUS_JSON]\n\n# APPENDIX A\n译文'
                ),
            ),
        ):
            result = asyncio.run(translation.handle_continue_translation("task-2", payload))

        self.assertEqual(result["translation_status"]["current_unit_id"], "APPENDIX A")
        self.assertEqual(result["translation_status"]["state"], "ALL_DONE")
        self.assertEqual(result["translation_status"]["active_scope"], "done")
        self.assertEqual(result["translation_status"]["next_unit_id"], "")


if __name__ == "__main__":
    unittest.main()
