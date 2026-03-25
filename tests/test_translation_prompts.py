from __future__ import annotations

import unittest

from backend.core.config import settings
from backend.services.translation_prompts import (
    build_command_block,
    build_continue_translation_prompt,
    build_initial_translation_prompt,
    build_input_status_block,
)


class TranslationPromptsTests(unittest.TestCase):
    def test_build_command_block(self) -> None:
        self.assertEqual(
            build_command_block("continue", "body"),
            "[COMMAND]\naction=continue\ntarget=body\n[/COMMAND]",
        )

    def test_build_input_status_block(self) -> None:
        block = build_input_status_block(
            {
                "scope": "body_only",
                "completed": "Abstract",
                "current": "1 Introduction",
                "next": "2 Method",
                "remaining": "2 Method",
                "state": "IN_PROGRESS",
                "phase": "body",
            }
        )
        self.assertIn("[INPUT_STATUS]", block)
        self.assertIn("completed=Abstract", block)
        self.assertIn("phase=body", block)

    def test_build_continue_translation_prompt_replaces_placeholders(self) -> None:
        prompt = build_continue_translation_prompt(
            settings.continue_prompt,
            translation_status={
                "scope": "body_only",
                "completed": "Abstract",
                "current": "1 Introduction",
                "next": "2 Method",
                "remaining": "2 Method",
                "state": "IN_PROGRESS",
                "phase": "body",
            },
            action="continue",
            target_scope="body",
        )
        self.assertNotIn("<<INPUT_STATUS_BLOCK>>", prompt)
        self.assertNotIn("<<COMMAND_BLOCK>>", prompt)
        self.assertIn("[INPUT_STATUS]", prompt)
        self.assertIn("action=continue", prompt)
        self.assertIn("target=body", prompt)

    def test_build_initial_translation_prompt_returns_trimmed_text(self) -> None:
        prompt = build_initial_translation_prompt(f"  {settings.initial_prompt}\n")
        self.assertTrue(prompt.startswith("你是学术论文翻译助手。"))
        self.assertTrue(prompt.endswith("[/COMMAND]"))

    def test_build_continue_translation_prompt_appends_missing_placeholders(self) -> None:
        prompt = build_continue_translation_prompt(
            "续翻模板",
            translation_status={
                "scope": "body_only",
                "completed": "Abstract",
                "current": "1 Introduction",
                "next": "2 Method",
                "remaining": "2 Method",
                "state": "IN_PROGRESS",
                "phase": "body",
            },
            action="continue",
            target_scope="body",
        )
        self.assertIn("[INPUT_STATUS]", prompt)
        self.assertIn("[COMMAND]", prompt)


if __name__ == "__main__":
    unittest.main()
