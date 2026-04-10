from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from backend.domain.message_payloads import build_translation_status_payload, normalize_translation_plan_payload
from backend.modules.conversations import add_message, build_conversation_list_item
from backend.platform.models import Conversation


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


if __name__ == "__main__":
    unittest.main()
