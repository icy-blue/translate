from __future__ import annotations

import unittest

from backend.domain.message_payloads import (
    build_translation_status_payload,
    build_unit_translation_prompt,
    normalize_translation_plan_payload,
    parse_translation_plan_response,
    preprocess_bot_reply_for_storage,
)
from backend.platform.config import settings


class MessagePayloadsTest(unittest.TestCase):
    def test_parse_translation_plan_response_extracts_units_and_appendices(self):
        raw = """
{
  "status": "ok",
  "units": ["ABSTRACT", "1 INTRODUCTION", "3 METHOD :: 3.1 Setup"],
  "appendix_units": ["APPENDIX A DETAILS"],
  "reason": ""
}
        """.strip()
        parsed = parse_translation_plan_response(raw)
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["units"][0], "ABSTRACT")
        self.assertEqual(parsed["appendix_units"], ["APPENDIX A DETAILS"])

    def test_normalize_translation_plan_unsupported_clears_units(self):
        normalized = normalize_translation_plan_payload(
            {
                "status": "unsupported",
                "units": ["ABSTRACT"],
                "appendix_units": ["APPENDIX A"],
                "reason": "ambiguous_structure",
            }
        )
        self.assertEqual(normalized["status"], "unsupported")
        self.assertEqual(normalized["units"], [])
        self.assertEqual(normalized["appendix_units"], [])
        self.assertEqual(normalized["reason"], "ambiguous_structure")

    def test_preprocess_bot_reply_strips_status_json_and_preserves_canonical_payload(self):
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
            completed_unit_ids=["ABSTRACT"],
            current_unit_id="ABSTRACT",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "ABSTRACT", "state": "OK", "reason": ""},
        )
        raw = """
[TRANSLATION_STATUS_JSON]
{
  "current_unit_id": "ABSTRACT",
  "state": "OK",
  "reason": ""
}
[/TRANSLATION_STATUS_JSON]

# 摘要
这是译文。
        """.strip()
        prepared = preprocess_bot_reply_for_storage(
            raw,
            {"translation_plan": translation_plan, "translation_status": translation_status},
        )
        self.assertEqual(prepared["translation_status"]["current_unit_id"], "ABSTRACT")
        self.assertEqual(prepared["translation_plan"]["units"], ["ABSTRACT", "1 INTRODUCTION"])
        self.assertNotIn("[TRANSLATION_STATUS_JSON]", prepared["content"])
        self.assertIn("这是译文。", prepared["content"])

    def test_build_translation_status_payload_marks_body_done_when_appendix_remains(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT", "1 INTRODUCTION"],
                "appendix_units": ["APPENDIX A"],
                "reason": "",
            }
        )
        status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["ABSTRACT", "1 INTRODUCTION"],
            current_unit_id="1 INTRODUCTION",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "1 INTRODUCTION", "state": "OK", "reason": ""},
        )
        self.assertEqual(status["state"], "BODY_DONE")
        self.assertEqual(status["active_scope"], "appendix")
        self.assertEqual(status["next_unit_id"], "APPENDIX A")

    def test_build_unit_translation_prompt_injects_units_and_current_unit(self):
        prompt = build_unit_translation_prompt(
            "ACTIVE_UNITS:\n<<ACTIVE_UNITS_JSON>>\nCURRENT_UNIT_ID:\n<<CURRENT_UNIT_ID>>",
            active_units=["ABSTRACT", "1 INTRODUCTION"],
            current_unit_id="1 INTRODUCTION",
        )
        self.assertIn('"ABSTRACT"', prompt)
        self.assertIn("1 INTRODUCTION", prompt)

    def test_prompts_include_planner_and_heading_rules(self):
        self.assertIn("translation-plan extractor", settings.initial_prompt)
        self.assertIn('"appendix_units"', settings.initial_prompt)
        self.assertIn("first subsection", settings.continue_prompt)
        self.assertIn("# 摘要", settings.continue_prompt)
        self.assertIn("Second-level section headings must use `##`", settings.continue_prompt)
        self.assertIn("translate only the heading text after that prefix", settings.continue_prompt)
        self.assertIn("`III.`", settings.continue_prompt)


if __name__ == "__main__":
    unittest.main()
