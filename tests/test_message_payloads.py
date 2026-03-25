from __future__ import annotations

import unittest

from backend.domain.message_payloads import (
    build_continue_translation_prompt,
    preprocess_bot_reply_for_storage,
)


class MessagePayloadsTest(unittest.TestCase):
    def test_preprocess_bot_reply_extracts_translation_status(self):
        raw = """
[TRANSLATION_STATUS]
scope=body_only
completed=摘要
current=摘要
next=1 Introduction
remaining=1 Introduction
state=IN_PROGRESS
phase=body
available_scope_extensions=appendix,references
next_action_type=continue
next_action_command=[COMMAND]
action=continue
target=body
[/COMMAND]
next_action_target_scope=body
recommended_stop_reason=unsupported
[/TRANSLATION_STATUS]

# 摘要
这是译文。
        """.strip()
        prepared = preprocess_bot_reply_for_storage(raw)
        self.assertEqual(prepared["translation_status"]["state"], "IN_PROGRESS")
        self.assertIn("appendix", prepared["translation_status"]["available_scope_extensions"])
        self.assertIn("这是译文。", prepared["content"])

    def test_continue_prompt_injects_status_and_command(self):
        prompt = build_continue_translation_prompt(
            "prefix\n<<INPUT_STATUS_BLOCK>>\n<<COMMAND_BLOCK>>",
            translation_status={
                "scope": "body_only",
                "completed": "摘要",
                "current": "摘要",
                "next": "1 Introduction",
                "remaining": "1 Introduction",
                "state": "IN_PROGRESS",
                "phase": "body",
            },
            action="continue",
            target_scope="body",
        )
        self.assertIn("[INPUT_STATUS]", prompt)
        self.assertIn("state=IN_PROGRESS", prompt)
        self.assertIn("[COMMAND]", prompt)
        self.assertIn("target=body", prompt)


if __name__ == "__main__":
    unittest.main()
