from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlmodel import SQLModel, Session, create_engine, select

from backend.platform.models import Conversation, Message
from scripts import backfill_formula_text_artifacts as backfill


class FormulaTextBackfillTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.addCleanup(Path(self.db_file.name).unlink, missing_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_file.name}")
        SQLModel.metadata.create_all(self.engine)

    def _seed_message(self, content: str, *, message_id: int | None = None) -> int:
        with Session(self.engine) as session:
            if session.get(Conversation, "conv-1") is None:
                session.add(Conversation(id="conv-1", title="Paper"))
            message = Message(
                id=message_id,
                conversation_id="conv-1",
                message_kind="bot_reply",
                visible_to_user=True,
                content=content,
            )
            session.add(message)
            session.commit()
            session.refresh(message)
            return message.id or 0

    def test_repair_unicode_and_named_artifacts(self):
        text = "点集 /u1D443 和 /u1D7061，/summationdisplay.1 /barex x /bardblex y .alt"
        repaired = backfill.repair_text(text)
        self.assertIn("𝑃", repaired)
        self.assertIn("𝜆1", repaired)
        self.assertIn("∑", repaired)
        self.assertIn("| x", repaired)
        self.assertIn("‖ y", repaired)
        self.assertNotIn("/u1D443", repaired)
        self.assertNotIn(".alt", repaired)

    def test_repair_v0xk_subscript_token(self):
        text = "L<|v0xK|tri|v0xK|> and f̃<|v0xK|pᵢ|v0xK|>"
        self.assertEqual(backfill.repair_text(text), "L_{tri} and f̃_{pᵢ}")

    def test_build_audit_row_keep_for_clean_message(self):
        message_id = self._seed_message("clean content")
        with Session(self.engine) as session:
            row = backfill.build_audit_row(session.get(Message, message_id))
        self.assertEqual(row.action, "keep")
        self.assertEqual(row.original_content, row.next_content)

    def test_dry_run_does_not_update_database(self):
        message_id = self._seed_message("bad /u1D443")
        args = type("Args", (), {"conversation_id": None, "message_id": message_id, "write": False, "output": "-"})()
        with patch.object(backfill, "engine", self.engine):
            with patch.object(backfill, "write_report"):
                backfill.main_with_args(args)

        with Session(self.engine) as session:
            self.assertEqual(session.get(Message, message_id).content, "bad /u1D443")

    def test_write_updates_database(self):
        message_id = self._seed_message("bad /u1D443")
        args = type("Args", (), {"conversation_id": None, "message_id": message_id, "write": True, "output": "-"})()
        with patch.object(backfill, "engine", self.engine):
            with patch.object(backfill, "write_report"):
                backfill.main_with_args(args)

        with Session(self.engine) as session:
            self.assertEqual(session.get(Message, message_id).content, "bad 𝑃")


if __name__ == "__main__":
    unittest.main()
