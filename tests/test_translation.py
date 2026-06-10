from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlmodel import SQLModel, Session, create_engine, select

from backend.domain.message_payloads import (
    build_translation_status_payload,
    normalize_translation_glossary_payload,
    normalize_translation_plan_payload,
    safe_json_loads,
)
from backend.modules import translation
from backend.modules.conversations import add_message
from backend.platform.models import Conversation, FileRecord, Message, PaperSemanticScholarResult


class ContinueTranslationFlowTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.addCleanup(Path(self.db_file.name).unlink, missing_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_file.name}")
        SQLModel.metadata.create_all(self.engine)

    def _seed_conversation(self, translation_plan: dict, translation_status: dict, translation_glossary: dict | None = None) -> None:
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
                client_payload={
                    "translation_plan": translation_plan,
                    "translation_status": translation_status,
                    "translation_glossary": translation_glossary,
                },
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
            patch.object(translation, "mark_task_progress") as progress_mock,
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
        progress_mock.assert_any_call("task-1", "等待 Poe 返回翻译结果")

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

    def test_continue_translation_passes_deepseek_provider(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT"],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[],
            current_unit_id="",
            attempted_scope="body",
            raw_translation_result=None,
        )
        self._seed_conversation(translation_plan, translation_status)

        payload = translation.ContinueTranslationTaskPayload(
            conversation_id="conv-1",
            action="continue",
            target_scope="body",
            provider="deepseek",
            poe_model="deepseek-v4-pro",
            api_key="deepseek-key",
        )
        with (
            patch.object(translation, "engine", self.engine),
            patch.object(translation, "mark_task_progress") as progress_mock,
            patch.object(
                translation,
                "get_bot_response",
                AsyncMock(
                    return_value='[TRANSLATION_STATUS_JSON]\n{"current_unit_id":"ABSTRACT","state":"OK","reason":""}\n[/TRANSLATION_STATUS_JSON]\n\n## Abstract\n译文'
                ),
            ) as response_mock,
        ):
            result = asyncio.run(translation.handle_continue_translation("task-deepseek", payload))

        self.assertEqual(result["translation_status"]["current_unit_id"], "ABSTRACT")
        self.assertEqual(response_mock.await_args.kwargs["provider"], "deepseek")
        with Session(self.engine) as session:
            bot_messages = session.exec(
                select(Message).where(Message.conversation_id == "conv-1", Message.message_kind == "bot_reply").order_by(Message.id)
            ).all()
            saved_payload = safe_json_loads(bot_messages[-1].client_payload_json, {})
            saved_content = bot_messages[-1].content
        self.assertNotIn("translation_provider", saved_payload)
        self.assertTrue(saved_content.startswith("# 摘要\n"))
        progress_mock.assert_any_call("task-deepseek", "等待 DeepSeek 返回翻译结果")

    def test_continue_translation_does_not_pass_semantic_arxiv_id_to_deepseek(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT"],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[],
            current_unit_id="",
            attempted_scope="body",
            raw_translation_result=None,
        )
        self._seed_conversation(translation_plan, translation_status)
        with Session(self.engine) as session:
            session.add(
                PaperSemanticScholarResult(
                    conversation_id="conv-1",
                    status="matched",
                    external_ids_json='{"ArXiv": "2605.10922v1"}',
                    raw_response_json="{}",
                )
            )
            session.commit()

        payload = translation.ContinueTranslationTaskPayload(
            conversation_id="conv-1",
            action="continue",
            target_scope="body",
            provider="deepseek",
            poe_model="deepseek-v4-pro",
            api_key="deepseek-key",
        )
        with (
            patch.object(translation, "engine", self.engine),
            patch.object(translation, "mark_task_progress"),
            patch.object(
                translation,
                "get_bot_response",
                AsyncMock(
                    return_value='[TRANSLATION_STATUS_JSON]\n{"current_unit_id":"ABSTRACT","state":"OK","reason":""}\n[/TRANSLATION_STATUS_JSON]\n\n# 摘要\n译文'
                ),
            ) as response_mock,
        ):
            asyncio.run(translation.handle_continue_translation("task-deepseek-arxiv", payload))

        self.assertNotIn("arxiv_id", response_mock.await_args.kwargs)

    def test_mixed_continue_translation_uses_poe_provider(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT"],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[],
            current_unit_id="",
            attempted_scope="body",
            raw_translation_result=None,
        )
        self._seed_conversation(translation_plan, translation_status)

        payload = translation.ContinueTranslationTaskPayload(
            conversation_id="conv-1",
            action="continue",
            target_scope="body",
            provider="mixed",
            poe_model="poe-model",
            poe_api_key="poe-key",
            deepseek_api_key="deepseek-key",
        )
        with (
            patch.object(translation, "engine", self.engine),
            patch.object(translation, "mark_task_progress") as progress_mock,
            patch.object(
                translation,
                "get_bot_response",
                AsyncMock(
                    return_value='[TRANSLATION_STATUS_JSON]\n{"current_unit_id":"ABSTRACT","state":"OK","reason":""}\n[/TRANSLATION_STATUS_JSON]\n\n## Abstract\n译文'
                ),
            ) as response_mock,
        ):
            result = asyncio.run(translation.handle_continue_translation("task-mixed", payload))

        self.assertEqual(result["translation_status"]["current_unit_id"], "ABSTRACT")
        self.assertEqual(response_mock.await_args.args[2], "poe-key")
        self.assertEqual(response_mock.await_args.kwargs["provider"], "poe")
        with Session(self.engine) as session:
            bot_messages = session.exec(
                select(Message).where(Message.conversation_id == "conv-1", Message.message_kind == "bot_reply").order_by(Message.id)
            ).all()
            saved_payload = safe_json_loads(bot_messages[-1].client_payload_json, {})
            saved_content = bot_messages[-1].content
        self.assertNotIn("translation_provider", saved_payload)
        self.assertTrue(saved_content.startswith("## Abstract\n"))
        progress_mock.assert_any_call("task-mixed", "等待 Poe 返回翻译结果")

    def test_continue_translation_rejects_unconfirmed_glossary(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT", "1 INTRODUCTION"],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[],
            current_unit_id="",
            attempted_scope="body",
            raw_translation_result=None,
        )
        translation_glossary = normalize_translation_glossary_payload(
            {
                "status": "draft",
                "entries": [{"term": "mesh face", "candidates": ["三角面片", "网格面"]}],
            }
        )
        self._seed_conversation(translation_plan, translation_status, translation_glossary)

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
        ):
            with self.assertRaisesRegex(Exception, "术语词表尚未确认"):
                asyncio.run(translation.handle_continue_translation("task-3", payload))

    def test_retry_translation_uses_previous_status_before_unsupported_failure(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT", "2 METHOD"],
                "appendix_units": [],
                "reason": "",
            }
        )
        previous_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["ABSTRACT"],
            current_unit_id="ABSTRACT",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "ABSTRACT", "state": "OK", "reason": ""},
        )
        self._seed_conversation(translation_plan, previous_status)
        failed_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["ABSTRACT"],
            current_unit_id="2 METHOD",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "2 METHOD", "state": "UNSUPPORTED", "reason": "missing text"},
        )
        with Session(self.engine) as session:
            add_message(
                session,
                conversation_id="conv-1",
                content="",
                message_kind="bot_reply",
                visible_to_user=True,
                client_payload={
                    "translation_plan": translation_plan,
                    "translation_status": failed_status,
                },
            )
            session.commit()

        payload = translation.ContinueTranslationTaskPayload(
            conversation_id="conv-1",
            action="retry",
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
                    return_value='[TRANSLATION_STATUS_JSON]\n{"current_unit_id":"2 METHOD","state":"OK","reason":""}\n[/TRANSLATION_STATUS_JSON]\n\n# 2 METHOD\n译文'
                ),
            ) as response_mock,
        ):
            result = asyncio.run(translation.handle_continue_translation("task-retry", payload))

        self.assertEqual(result["translation_status"]["current_unit_id"], "2 METHOD")
        self.assertEqual(result["translation_status"]["completed_unit_ids"], ["ABSTRACT", "2 METHOD"])
        prompt = response_mock.await_args.args[0][0].content
        self.assertIn("CURRENT_UNIT_ID:\n2 METHOD", prompt)

    def test_confirm_translation_glossary_persists_confirmed_payload(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT", "1 INTRODUCTION"],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[],
            current_unit_id="",
            attempted_scope="body",
            raw_translation_result=None,
        )
        translation_glossary = normalize_translation_glossary_payload(
            {
                "status": "draft",
                "entries": [{"term": "mesh face", "candidates": ["三角面片", "网格面"]}],
            }
        )
        self._seed_conversation(translation_plan, translation_status, translation_glossary)

        request_payload = translation.ConfirmTranslationGlossaryPayload(
            entries=[
                translation.TranslationGlossaryEntryPayload(
                    term="mesh face",
                    candidates=["三角面片", "网格面"],
                    selected="网格面",
                )
            ]
        )

        with patch.object(translation, "engine", self.engine):
            with Session(self.engine) as session:
                result = asyncio.run(
                    translation.confirm_translation_glossary_route(
                        "conv-1",
                        request_payload,
                        session=session,
                        _read_only=None,
                    )
                )

        self.assertEqual(result["translation_glossary"]["status"], "confirmed")
        self.assertEqual(result["translation_glossary"]["entries"][0]["selected"], "网格面")


if __name__ == "__main__":
    unittest.main()
