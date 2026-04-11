from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlmodel import SQLModel, Session, create_engine

from backend.modules import metadata
from backend.modules.conversations import add_message
from backend.platform.models import Conversation


class MetadataRefreshTest(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.addCleanup(Path(self.db_file.name).unlink, missing_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_file.name}")
        SQLModel.metadata.create_all(self.engine)

    def test_refresh_metadata_uses_semantic_abstract_when_translation_missing(self):
        semantic_result = SimpleNamespace(
            abstract="A semantic abstract fallback for tagging.",
            venue_abbr="",
            ccf_category="None",
            ccf_type="None",
            citation_count=None,
            venue=None,
            year=None,
            updated_at=datetime.now(timezone.utc),
        )

        with Session(self.engine) as session:
            session.add(Conversation(id="conv-1", title="Paper", original_filename="paper.pdf"))
            add_message(
                session,
                conversation_id="conv-1",
                content="",
                message_kind="bot_reply",
                visible_to_user=True,
                client_payload={},
            )
            session.commit()

            with (
                patch.object(metadata, "refresh_conversation_semantic_result", return_value=semantic_result),
                patch.object(metadata, "extract_and_store_tags", AsyncMock(return_value=[])) as extract_tags_mock,
            ):
                asyncio.run(metadata.refresh_conversation_metadata(session, "conv-1", "tag-model", "test-key"))

        extract_tags_mock.assert_awaited_once()
        self.assertEqual(extract_tags_mock.await_args.kwargs["first_bot_message"], "")
        self.assertEqual(extract_tags_mock.await_args.kwargs["fallback_abstract"], "A semantic abstract fallback for tagging.")


if __name__ == "__main__":
    unittest.main()
