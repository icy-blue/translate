from __future__ import annotations

import json
import unittest

from backend.persistence.models import Message
from scripts.backfill_translation_payload_v2 import (
    build_next_payload,
    extract_candidate_status,
    normalize_status_to_v2,
)


class BackfillTranslationPayloadV2Tests(unittest.TestCase):
    def test_existing_payload_natural_language_command_maps_to_structured_next_action(self) -> None:
        message = Message(
            id=1,
            conversation_id="conv1",
            message_kind="bot_reply",
            content="# 摘要\n内容",
            client_payload_json=json.dumps(
                {
                    "translation_status": {
                        "scope": "body_only",
                        "completed": "Abstract",
                        "current": "Abstract",
                        "next": "1 Introduction",
                        "remaining": "1 Introduction; 2 Method",
                        "state": "IN_PROGRESS",
                        "phase": "body",
                        "available_scope_extensions": "appendix,acknowledgements,references",
                        "next_action_command": "继续翻译附录",
                        "recommended_stop_reason": "body_done",
                    }
                },
                ensure_ascii=False,
            ),
        )

        payload, reason = build_next_payload(message)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["translation_status"]["next_action"]["type"], "continue")
        self.assertEqual(payload["translation_status"]["next_action"]["target_scope"], "appendix")
        self.assertIn("action=continue", payload["translation_status"]["next_action"]["command"])

    def test_content_only_status_block_can_be_backfilled(self) -> None:
        message = Message(
            id=2,
            conversation_id="conv2",
            message_kind="bot_reply",
            content=(
                "[TRANSLATION_STATUS]\n"
                "scope=body_only\n"
                "completed=Abstract\n"
                "current=Abstract\n"
                "next=1 Introduction\n"
                "remaining=1 Introduction; 2 Method\n"
                "state=IN_PROGRESS\n"
                "phase=body\n"
                "available_scope_extensions=appendix,acknowledgements,references\n"
                "next_action_type=continue\n"
                "next_action_command=继续\n"
                "next_action_target_scope=body\n"
                "recommended_stop_reason=body_done\n"
                "[/TRANSLATION_STATUS]\n\n"
                "# 摘要\n内容"
            ),
            client_payload_json=None,
        )

        candidate_status, source = extract_candidate_status(message)
        normalized, reason = normalize_status_to_v2(candidate_status)

        self.assertEqual(source, "content.translation_status_block")
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertEqual(normalized["state"], "IN_PROGRESS")
        self.assertEqual(normalized["next_action"]["target_scope"], "body")

    def test_multiline_command_block_is_parsed(self) -> None:
        message = Message(
            id=3,
            conversation_id="conv3",
            message_kind="bot_reply",
            content=(
                "[TRANSLATION_STATUS]\n"
                "scope=body_only\n"
                "completed=Abstract\n"
                "current=1 Introduction\n"
                "next=2 Method\n"
                "remaining=2 Method; 3 Experiments\n"
                "state=IN_PROGRESS\n"
                "phase=body\n"
                "available_scope_extensions=appendix,acknowledgements,references\n"
                "next_action_command=[COMMAND]\n"
                "action=continue\n"
                "target=body\n"
                "[/COMMAND]\n"
                "recommended_stop_reason=body_done\n"
                "[/TRANSLATION_STATUS]\n\n"
                "# 1 Introduction\n内容"
            ),
            client_payload_json=None,
        )

        payload, reason = build_next_payload(message)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["translation_status"]["next_action"]["type"], "continue")
        self.assertEqual(payload["translation_status"]["next_action"]["target_scope"], "body")

    def test_invalid_state_returns_skip_error(self) -> None:
        message = Message(
            id=4,
            conversation_id="conv4",
            message_kind="bot_reply",
            content="# 内容",
            client_payload_json=json.dumps(
                {
                    "translation_status": {
                        "scope": "body_only",
                        "state": "",
                    }
                },
                ensure_ascii=False,
            ),
        )

        payload, reason = build_next_payload(message)
        self.assertIsNone(payload)
        self.assertIn("invalid_state", reason)

    def test_already_canonical_payload_is_idempotent(self) -> None:
        canonical_payload = {
            "translation_status": {
                "scope": "body_only",
                "completed": "Abstract",
                "current": "1 Introduction",
                "next": "2 Method",
                "remaining": "2 Method; 3 Experiments",
                "state": "IN_PROGRESS",
                "phase": "body",
                "available_scope_extensions": ["appendix", "acknowledgements", "references"],
                "next_action": {
                    "type": "continue",
                    "command": "[COMMAND]\naction=continue\ntarget=body\n[/COMMAND]",
                    "target_scope": "body",
                },
                "extension_commands": {
                    "appendix": "继续翻译附录",
                    "acknowledgements": "继续翻译致谢",
                    "references": "继续翻译参考文献",
                },
                "recommended_stop_reason": "body_done",
                "source": "backfill_v2",
                "is_completed": False,
                "is_all_done": False,
            }
        }
        message = Message(
            id=5,
            conversation_id="conv5",
            message_kind="bot_reply",
            content="# 1 Introduction\n内容",
            client_payload_json=json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True),
        )

        payload, reason = build_next_payload(message)
        self.assertEqual(reason, "ok")
        self.assertEqual(payload, canonical_payload)

    def test_document_outline_is_backfilled_after_status_block_is_removed(self) -> None:
        message = Message(
            id=6,
            conversation_id="conv6",
            message_kind="bot_reply",
            content=(
                "[TRANSLATION_STATUS]\n"
                "scope=body_only\n"
                "completed=Abstract\n"
                "current=Abstract\n"
                "next=1 Introduction\n"
                "remaining=1 Introduction\n"
                "state=IN_PROGRESS\n"
                "phase=body\n"
                "available_scope_extensions=appendix,acknowledgements,references\n"
                "next_action_type=continue\n"
                "next_action_target_scope=body\n"
                "recommended_stop_reason=body_done\n"
                "[/TRANSLATION_STATUS]\n\n"
                "结构概览\n"
                "- Abstract\n"
                "- 1 Introduction\n\n"
                "# 摘要\n内容"
            ),
            client_payload_json=None,
        )

        payload, reason = build_next_payload(message)
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["document_outline"]["title"], "结构概览")
        self.assertIn("- Abstract", payload["document_outline"]["content"])


if __name__ == "__main__":
    unittest.main()
