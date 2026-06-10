from __future__ import annotations

import unittest

from backend.domain.message_payloads import (
    build_translation_status_payload,
    build_unit_translation_prompt,
    normalize_translation_glossary_payload,
    normalize_translation_plan_payload,
    parse_translation_glossary_response,
    parse_translation_plan_response,
    preprocess_bot_reply_for_storage,
)
from backend.platform.config import settings


class MessagePayloadsTest(unittest.TestCase):
    def _prepare_translated_unit(self, unit_id: str, visible_content: str, *, translation_provider: str = "deepseek") -> str:
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": [unit_id],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[unit_id],
            current_unit_id=unit_id,
            attempted_scope="body",
            raw_translation_result={"current_unit_id": unit_id, "state": "OK", "reason": ""},
        )
        raw = f"""
[TRANSLATION_STATUS_JSON]
{{"current_unit_id":"{unit_id}","state":"OK","reason":""}}
[/TRANSLATION_STATUS_JSON]

{visible_content}
        """.strip()
        prepared = preprocess_bot_reply_for_storage(
            raw,
            {
                "translation_plan": translation_plan,
                "translation_status": translation_status,
                "translation_provider": translation_provider,
            },
        )
        return prepared["content"]

    def test_parse_translation_plan_response_extracts_units_and_appendices(self):
        raw = """
{
  "status": "ok",
  "units": ["ABSTRACT", "1 INTRODUCTION", "3 METHOD :: 3.1 Setup"],
  "appendix_units": ["APPENDIX A DETAILS"],
  "reason": "",
  "glossary": [
    {"term": "mesh face", "candidates": ["三角面片", "网格面"]}
  ]
}
        """.strip()
        parsed = parse_translation_plan_response(raw)
        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["units"][0], "ABSTRACT")
        self.assertEqual(parsed["appendix_units"], ["APPENDIX A DETAILS"])

    def test_parse_translation_glossary_response_extracts_entries(self):
        raw = """
{
  "status": "ok",
  "units": ["ABSTRACT"],
  "appendix_units": [],
  "reason": "",
  "glossary": [
    {"term": "mesh face", "candidates": ["三角面片", "网格面", "面片", "多余候选"]},
    {"term": "NeRF", "candidates": ["神经辐射场"]}
  ]
}
        """.strip()
        parsed = parse_translation_glossary_response(raw)
        self.assertEqual(parsed["status"], "draft")
        self.assertEqual(parsed["entries"][0]["term"], "mesh face")
        self.assertEqual(parsed["entries"][0]["candidates"], ["三角面片", "网格面", "面片"])
        self.assertEqual(parsed["entries"][0]["selected"], "三角面片")

    def test_normalize_translation_glossary_defaults_empty_to_confirmed(self):
        normalized = normalize_translation_glossary_payload({"status": "draft", "entries": []})
        self.assertEqual(normalized["status"], "confirmed")
        self.assertEqual(normalized["entries"], [])

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

    def test_normalize_translation_plan_removes_wrapper_and_descendant_duplicates(self):
        normalized = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["ABSTRACT", "1 METHOD", "1.1 Setup", "1.2 Training"],
                "appendix_units": [
                    "Supplementary Material",
                    "A. Additional Implementation Details",
                    "A.1. SpaceDrive Framework",
                    "A.2. Training Details",
                    "B. Additional Experiments and Analyses",
                    "B.1. More Ablation Studies",
                ],
                "reason": "",
            }
        )
        self.assertEqual(normalized["units"], ["ABSTRACT", "1 METHOD"])
        self.assertEqual(
            normalized["appendix_units"],
            [
                "A. Additional Implementation Details",
                "B. Additional Experiments and Analyses",
            ],
        )

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
        translation_glossary = normalize_translation_glossary_payload(
            {
                "status": "confirmed",
                "entries": [{"term": "mesh face", "candidates": ["三角面片", "网格面"], "selected": "网格面"}],
            }
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
            {
                "translation_plan": translation_plan,
                "translation_status": translation_status,
                "translation_glossary": translation_glossary,
                "translation_provider": "deepseek",
            },
        )
        self.assertEqual(prepared["translation_status"]["current_unit_id"], "ABSTRACT")
        self.assertEqual(prepared["translation_plan"]["units"], ["ABSTRACT", "1 INTRODUCTION"])
        self.assertEqual(prepared["translation_glossary"]["entries"][0]["selected"], "网格面")
        self.assertNotIn("[TRANSLATION_STATUS_JSON]", prepared["content"])
        self.assertIn("这是译文。", prepared["content"])

    def test_preprocess_normalizes_top_level_markdown_heading_level(self):
        content = self._prepare_translated_unit("1 INTRODUCTION", "## 1 引言\n这是引言译文。")

        self.assertTrue(content.startswith("# 1 引言\n"))
        self.assertNotIn("## 1 引言", content)

    def test_preprocess_does_not_normalize_heading_without_deepseek_provider(self):
        content = self._prepare_translated_unit("1 INTRODUCTION", "## 1 引言\n这是引言译文。", translation_provider="poe")

        self.assertTrue(content.startswith("## 1 引言\n"))

    def test_preprocess_adds_top_level_markdown_to_plain_heading(self):
        content = self._prepare_translated_unit("1 INTRODUCTION", "1 引言\n这是引言译文。")

        self.assertTrue(content.startswith("# 1 引言\n"))

    def test_preprocess_normalizes_split_unit_single_heading_as_second_level(self):
        content = self._prepare_translated_unit("3 METHOD :: 3.1 Setup", "### 3.1 设置\n这是设置译文。")

        self.assertTrue(content.startswith("## 3.1 设置\n"))
        self.assertNotIn("### 3.1 设置", content)

    def test_preprocess_normalizes_split_unit_parent_and_child_headings(self):
        content = self._prepare_translated_unit("3 METHOD :: 3.1 Setup", "## 3 方法\n3.1 设置\n这是设置译文。")

        self.assertTrue(content.startswith("# 3 方法\n## 3.1 设置\n"))

    def test_preprocess_normalizes_split_parent_and_later_child_heading(self):
        content = self._prepare_translated_unit(
            "Experiments :: Unconditional video modeling",
            "## 4 实验\n\n本节先概述实验设置。\n\n### 4.1 无条件视频建模\n这是实验译文。",
        )

        self.assertIn("# 4 实验", content)
        self.assertIn("## 4.1 无条件视频建模", content)
        self.assertNotIn("### 4.1 无条件视频建模", content)

    def test_preprocess_demotes_unnumbered_markdown_heading_in_split_lead_in(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["Method :: Text-to-Image Model", "Method :: Spatiotemporal Layers"],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["Method :: Text-to-Image Model"],
            current_unit_id="Method :: Text-to-Image Model",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "Method :: Text-to-Image Model", "state": "OK", "reason": ""},
        )
        raw = """
[TRANSLATION_STATUS_JSON]
{"current_unit_id":"Method :: Text-to-Image Model","state":"OK","reason":""}
[/TRANSLATION_STATUS_JSON]

# 3 方法

## Make-A-Video 的最终文本到视频推理方案（如图 2 所示）可形式化为：

## 3.1 文本到图像模型
这是 3.1 译文。
        """.strip()
        prepared = preprocess_bot_reply_for_storage(
            raw,
            {
                "translation_plan": translation_plan,
                "translation_status": translation_status,
                "translation_provider": "deepseek",
            },
        )

        self.assertIn("# 3 方法", prepared["content"])
        self.assertIn("Make-A-Video 的最终文本到视频推理方案", prepared["content"])
        self.assertNotIn("## Make-A-Video 的最终文本到视频推理方案", prepared["content"])
        self.assertIn("## 3.1 文本到图像模型", prepared["content"])

    def test_preprocess_strips_repeated_parent_prelude_for_later_split_unit(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["Method :: Text-to-Image Model", "Method :: Spatiotemporal Layers"],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["Method :: Text-to-Image Model", "Method :: Spatiotemporal Layers"],
            current_unit_id="Method :: Spatiotemporal Layers",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "Method :: Spatiotemporal Layers", "state": "OK", "reason": ""},
        )
        raw = """
[TRANSLATION_STATUS_JSON]
{"current_unit_id":"Method :: Spatiotemporal Layers","state":"OK","reason":""}
[/TRANSLATION_STATUS_JSON]

# 3 方法

...

## 3.2 时空层

这是 3.2 译文。

#### 3.2.1 伪三维卷积层
这是 3.2.1 译文。
        """.strip()
        prepared = preprocess_bot_reply_for_storage(
            raw,
            {
                "translation_plan": translation_plan,
                "translation_status": translation_status,
                "translation_provider": "deepseek",
            },
        )

        self.assertTrue(prepared["content"].startswith("## 3.2 时空层\n"))
        self.assertNotIn("# 3 方法", prepared["content"])
        self.assertNotIn("...", prepared["content"])
        self.assertIn("### 3.2.1 伪三维卷积层", prepared["content"])

    def test_preprocess_normalizes_deeper_numbered_headings_by_numeric_depth(self):
        content = self._prepare_translated_unit(
            "Experiments :: Text-conditioned video generation",
            "### 4.3 文本条件视频生成\n\n正文。\n\n#### 4.3.1 视频与图像建模的联合训练\n更多正文。",
        )

        self.assertIn("## 4.3 文本条件视频生成", content)
        self.assertIn("### 4.3.1 视频与图像建模的联合训练", content)
        self.assertNotIn("#### 4.3.1 视频与图像建模的联合训练", content)

    def test_preprocess_infers_missing_top_level_number_from_active_units(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": [
                    "Abstract",
                    "Introduction",
                    "Background",
                    "Video diffusion models",
                    "Experiments :: Unconditional video modeling",
                    "Experiments :: Video prediction",
                    "Experiments :: Text-conditioned video generation",
                    "Related work",
                    "Conclusion",
                ],
                "appendix_units": [],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[
                "Abstract",
                "Introduction",
                "Background",
                "Video diffusion models",
                "Experiments :: Unconditional video modeling",
                "Experiments :: Video prediction",
                "Experiments :: Text-conditioned video generation",
                "Related work",
            ],
            current_unit_id="Related work",
            attempted_scope="body",
            raw_translation_result={"current_unit_id": "Related work", "state": "OK", "reason": ""},
        )
        raw = """
[TRANSLATION_STATUS_JSON]
{"current_unit_id":"Related work","state":"OK","reason":""}
[/TRANSLATION_STATUS_JSON]

# 相关工作
这是相关工作译文。
        """.strip()
        prepared = preprocess_bot_reply_for_storage(
            raw,
            {
                "translation_plan": translation_plan,
                "translation_status": translation_status,
                "translation_provider": "deepseek",
            },
        )

        self.assertTrue(prepared["content"].startswith("# 5 相关工作\n"))

    def test_preprocess_infers_appendix_letter_for_unnumbered_appendix_heading(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["Abstract", "Conclusion"],
                "appendix_units": ["Additional Implementation Details", "Model Samples"],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["Abstract", "Conclusion", "Additional Implementation Details"],
            current_unit_id="Additional Implementation Details",
            attempted_scope="appendix",
            raw_translation_result={"current_unit_id": "Additional Implementation Details", "state": "OK", "reason": ""},
        )
        raw = """
[TRANSLATION_STATUS_JSON]
{"current_unit_id":"Additional Implementation Details","state":"OK","reason":""}
[/TRANSLATION_STATUS_JSON]

# 附加实现细节
这是附录译文。
        """.strip()
        prepared = preprocess_bot_reply_for_storage(
            raw,
            {
                "translation_plan": translation_plan,
                "translation_status": translation_status,
                "translation_provider": "deepseek",
            },
        )

        self.assertTrue(prepared["content"].startswith("# A. 附加实现细节\n"))
        self.assertNotIn("# 1 附加实现细节", prepared["content"])

    def test_preprocess_replaces_numeric_appendix_heading_with_letter(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["Abstract", "Conclusion"],
                "appendix_units": [
                    "Additional Implementation Details",
                    "Model Samples",
                    "Additional Scaling Results",
                    "VAE Decoder Ablations",
                ],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=["Abstract", "Conclusion", "Additional Implementation Details", "Model Samples", "Additional Scaling Results"],
            current_unit_id="Additional Scaling Results",
            attempted_scope="appendix",
            raw_translation_result={"current_unit_id": "Additional Scaling Results", "state": "OK", "reason": ""},
        )
        raw = """
[TRANSLATION_STATUS_JSON]
{"current_unit_id":"Additional Scaling Results","state":"OK","reason":""}
[/TRANSLATION_STATUS_JSON]

# 3 附加缩放结果
这是附录译文。
        """.strip()
        prepared = preprocess_bot_reply_for_storage(
            raw,
            {
                "translation_plan": translation_plan,
                "translation_status": translation_status,
                "translation_provider": "deepseek",
            },
        )

        self.assertTrue(prepared["content"].startswith("# C. 附加缩放结果\n"))
        self.assertNotIn("# 3 附加缩放结果", prepared["content"])

    def test_preprocess_infers_appendix_letter_after_all_done_status(self):
        translation_plan = normalize_translation_plan_payload(
            {
                "status": "ok",
                "units": ["Abstract", "Conclusion"],
                "appendix_units": [
                    "Additional Implementation Details",
                    "Model Samples",
                    "Additional Scaling Results",
                    "VAE Decoder Ablations",
                ],
                "reason": "",
            }
        )
        translation_status = build_translation_status_payload(
            translation_plan,
            completed_unit_ids=[
                "Abstract",
                "Conclusion",
                "Additional Implementation Details",
                "Model Samples",
                "Additional Scaling Results",
                "VAE Decoder Ablations",
            ],
            current_unit_id="VAE Decoder Ablations",
            attempted_scope="appendix",
            raw_translation_result={"current_unit_id": "VAE Decoder Ablations", "state": "OK", "reason": ""},
        )
        raw = """
[TRANSLATION_STATUS_JSON]
{"current_unit_id":"VAE Decoder Ablations","state":"OK","reason":""}
[/TRANSLATION_STATUS_JSON]

# 10 VAE解码器消融实验
这是附录译文。
        """.strip()
        prepared = preprocess_bot_reply_for_storage(
            raw,
            {
                "translation_plan": translation_plan,
                "translation_status": translation_status,
                "translation_provider": "deepseek",
            },
        )

        self.assertTrue(prepared["content"].startswith("# D. VAE解码器消融实验\n"))
        self.assertNotIn("# 10 VAE解码器消融实验", prepared["content"])

    def test_preprocess_canonicalizes_abstract_heading(self):
        content = self._prepare_translated_unit("Abstract", "## Abstract\n这是摘要译文。")

        self.assertTrue(content.startswith("# 摘要\n"))
        self.assertNotIn("## Abstract", content)

    def test_preprocess_canonicalizes_spaced_abstract_unit_heading(self):
        content = self._prepare_translated_unit("A BSTRACT", "# 1 摘要\n这是摘要译文。")

        self.assertTrue(content.startswith("# 摘要\n"))
        self.assertNotIn("# 1 摘要", content)

    def test_preprocess_does_not_rewrite_opening_body_paragraph(self):
        content = self._prepare_translated_unit("1 INTRODUCTION", "这是第一段译文，没有标题。\n第二段译文。")

        self.assertEqual(content, "这是第一段译文，没有标题。\n第二段译文。")

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
            "GLOSSARY:\n<<CONFIRMED_GLOSSARY_JSON>>\nACTIVE_UNITS:\n<<ACTIVE_UNITS_JSON>>\nCURRENT_UNIT_ID:\n<<CURRENT_UNIT_ID>>",
            active_units=["ABSTRACT", "1 INTRODUCTION"],
            current_unit_id="1 INTRODUCTION",
            translation_glossary=normalize_translation_glossary_payload(
                {
                    "status": "confirmed",
                    "entries": [{"term": "mesh face", "candidates": ["三角面片", "网格面"], "selected": "三角面片"}],
                }
            ),
        )
        self.assertIn('"mesh face"', prompt)
        self.assertIn('"translation": "三角面片"', prompt)
        self.assertIn('"ABSTRACT"', prompt)
        self.assertIn("1 INTRODUCTION", prompt)

    def test_prompts_include_planner_and_heading_rules(self):
        self.assertIn("translation-plan extractor", settings.initial_prompt)
        self.assertIn('"appendix_units"', settings.initial_prompt)
        self.assertIn('"glossary"', settings.initial_prompt)
        self.assertIn("Prefer coarser units", settings.initial_prompt)
        self.assertIn("Never output both a parent heading", settings.initial_prompt)
        self.assertIn("Do not keep a generic wrapper heading like `Supplementary Material`", settings.initial_prompt)
        self.assertIn("first subsection", settings.continue_prompt)
        self.assertIn("# 摘要", settings.continue_prompt)
        self.assertIn("Second-level section headings must use `##`", settings.continue_prompt)
        self.assertIn("translate only the heading text after that prefix", settings.continue_prompt)
        self.assertIn("`III.`", settings.continue_prompt)
        self.assertIn("Wrap standalone or display equations in `\\[ ... \\]`", settings.continue_prompt)
        self.assertIn("Wrap inline mathematical expressions in `\\( ... \\)`", settings.continue_prompt)
        self.assertIn("do not translate tables themselves", settings.continue_prompt)
        self.assertIn("Translate only non-caption running prose", settings.continue_prompt)
        self.assertIn("no running prose remains after skipping those artifacts", settings.continue_prompt)
        self.assertIn("（本章仅图表）", settings.continue_prompt)
        self.assertIn("CONFIRMED_GLOSSARY_JSON", settings.continue_prompt)


if __name__ == "__main__":
    unittest.main()
