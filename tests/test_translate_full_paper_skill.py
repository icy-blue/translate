from __future__ import annotations

import asyncio
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


def _load_skill_module():
    skill_path = Path(__file__).resolve().parents[1] / "skills" / "translate-full-paper-skill" / "scripts" / "run.py"
    spec = importlib.util.spec_from_file_location("translate_full_paper_skill_run", skill_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load skill module from {skill_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


translate_full_paper_skill = _load_skill_module()


class TranslateFullPaperSkillTest(unittest.TestCase):
    def test_supported_translation_outputs_backend_compatible_messages(self):
        payload = {
            "api_key": "test-key",
            "poe_model": "test-model",
            "continue_count": 1,
            "poe_attachment": {
                "url": "https://example.invalid/paper.pdf",
                "content_type": "application/pdf",
                "name": "paper.pdf",
            },
        }
        planner_reply = """
{
  "status": "ok",
  "units": ["ABSTRACT", "1 INTRODUCTION"],
  "appendix_units": [],
  "reason": "",
  "glossary": [
    {"term": "mesh face", "candidates": ["三角面片", "网格面"]}
  ]
}
        """.strip()
        first_reply = """
[TRANSLATION_STATUS_JSON]
{"current_unit_id":"ABSTRACT","state":"OK","reason":""}
[/TRANSLATION_STATUS_JSON]

# 摘要
这是摘要译文。
        """.strip()
        second_reply = """
[TRANSLATION_STATUS_JSON]
{"current_unit_id":"1 INTRODUCTION","state":"OK","reason":""}
[/TRANSLATION_STATUS_JSON]

# 1 引言
这是引言译文。
        """.strip()

        with patch.object(
            translate_full_paper_skill,
            "get_bot_response",
            AsyncMock(side_effect=[planner_reply, first_reply, second_reply]),
        ):
            result = asyncio.run(translate_full_paper_skill._run(payload))

        self.assertTrue(result["ok"])
        self.assertEqual(result["continue_count_used"], 1)
        self.assertEqual(len(result["messages"]), 4)

        initial_user = result["messages"][0]
        first_bot = result["messages"][1]
        continue_user = result["messages"][2]
        second_bot = result["messages"][3]

        self.assertEqual(initial_user["message_kind"], "system_prompt")
        self.assertFalse(initial_user["visible_to_user"])
        self.assertEqual(continue_user["message_kind"], "continue_command")
        self.assertFalse(continue_user["visible_to_user"])

        self.assertEqual(first_bot["message_kind"], "bot_reply")
        self.assertTrue(first_bot["visible_to_user"])
        self.assertNotIn("[TRANSLATION_STATUS_JSON]", first_bot["content"])
        self.assertIn("这是摘要译文。", first_bot["content"])
        self.assertEqual(result["first_bot_message"], first_bot["content"])

        self.assertEqual(first_bot["client_payload"]["translation_status"]["current_unit_id"], "ABSTRACT")
        self.assertEqual(first_bot["client_payload"]["translation_glossary"]["status"], "confirmed")
        self.assertEqual(first_bot["client_payload"]["translation_glossary"]["entries"][0]["selected"], "三角面片")
        self.assertEqual(second_bot["client_payload"]["translation_status"]["current_unit_id"], "1 INTRODUCTION")
        self.assertEqual(result["translation_status"]["state"], "ALL_DONE")

    def test_unsupported_plan_emits_backend_compatible_empty_bot_reply(self):
        payload = {
            "api_key": "test-key",
            "poe_attachment": {
                "url": "https://example.invalid/paper.pdf",
                "content_type": "application/pdf",
                "name": "paper.pdf",
            },
        }
        planner_reply = """
{
  "status": "unsupported",
  "units": [],
  "appendix_units": [],
  "reason": "planner_parse_failed"
}
        """.strip()

        with patch.object(
            translate_full_paper_skill,
            "get_bot_response",
            AsyncMock(return_value=planner_reply),
        ):
            result = asyncio.run(translate_full_paper_skill._run(payload))

        self.assertTrue(result["ok"])
        self.assertEqual(result["continue_count_used"], 0)
        self.assertEqual(result["first_bot_message"], "")
        self.assertEqual(len(result["messages"]), 2)
        self.assertEqual(result["messages"][0]["message_kind"], "system_prompt")
        self.assertEqual(result["messages"][1]["message_kind"], "bot_reply")
        self.assertEqual(result["messages"][1]["content"], "")
        self.assertEqual(result["messages"][1]["client_payload"]["translation_status"]["state"], "UNSUPPORTED")
        self.assertEqual(result["translation_plan"]["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
