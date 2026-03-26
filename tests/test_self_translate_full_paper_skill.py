from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.domain.message_payloads import normalize_translation_plan_payload, normalize_translation_status_payload


def _load_module(relative_path: str, module_name: str):
    module_path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


self_translate_skill = _load_module(
    "skills/self-translate-full-paper-skill/scripts/run.py",
    "self_translate_full_paper_skill_run",
)


class SelfTranslateFullPaperSkillBridgeTest(unittest.TestCase):
    def _run_main_with_payload(self, request_payload: dict, artifact_payload: dict | None = None) -> tuple[int, dict]:
        with tempfile.NamedTemporaryFile(suffix=".json") as input_fp, tempfile.NamedTemporaryFile(suffix=".json") as output_fp:
            request = dict(request_payload)
            if artifact_payload is not None:
                with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as artifact_fp:
                    artifact_path = Path(artifact_fp.name)
                self.addCleanup(artifact_path.unlink, missing_ok=True)
                artifact_path.write_text(json.dumps(artifact_payload, ensure_ascii=False), encoding="utf-8")
                request["agent_output_json"] = str(artifact_path)
            Path(input_fp.name).write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
            with patch("sys.argv", ["run.py", "--input-json", input_fp.name, "--output-json", output_fp.name]):
                exit_code = self_translate_skill.main()
            result = json.loads(Path(output_fp.name).read_text(encoding="utf-8"))
        return exit_code, result

    def test_bridge_accepts_valid_agent_artifact(self):
        artifact = {
            "messages": [
                {
                    "role": "user",
                    "content": "Translate ABSTRACT from the local PDF.",
                    "message_kind": "system_prompt",
                    "visible_to_user": False,
                },
                {
                    "role": "bot",
                    "content": "[TRANSLATION_STATUS_JSON]\n{\"current_unit_id\":\"ABSTRACT\",\"state\":\"OK\",\"reason\":\"\"}\n[/TRANSLATION_STATUS_JSON]\n\n# 摘要\n这是摘要译文。",
                    "message_kind": "bot_reply",
                    "visible_to_user": True,
                    "client_payload": {
                        "translation_plan": {
                            "status": "ok",
                            "units": ["ABSTRACT"],
                            "appendix_units": [],
                            "reason": "",
                        },
                        "translation_status": {
                            "protocol": "unit_v1",
                            "planner_status": "ok",
                            "active_scope": "done",
                            "active_units": [],
                            "current_unit_id": "ABSTRACT",
                            "current_unit_index": -1,
                            "completed_unit_ids": ["ABSTRACT"],
                            "remaining_unit_ids": [],
                            "next_unit_id": "",
                            "state": "ALL_DONE",
                            "reason": "",
                            "total_unit_count": 1,
                            "completed_unit_count": 1,
                            "source": "canonical_payload",
                            "is_completed": True,
                            "is_all_done": True,
                        },
                    },
                },
            ],
            "translation_plan": {
                "status": "ok",
                "units": ["ABSTRACT"],
                "appendix_units": [],
                "reason": "",
            },
            "translation_status": {
                "protocol": "unit_v1",
                "planner_status": "ok",
                "active_scope": "done",
                "active_units": [],
                "current_unit_id": "ABSTRACT",
                "current_unit_index": -1,
                "completed_unit_ids": ["ABSTRACT"],
                "remaining_unit_ids": [],
                "next_unit_id": "",
                "state": "ALL_DONE",
                "reason": "",
                "total_unit_count": 1,
                "completed_unit_count": 1,
                "source": "canonical_payload",
                "is_completed": True,
                "is_all_done": True,
            },
            "errors": [],
        }

        exit_code, result = self._run_main_with_payload({}, artifact)

        self.assertEqual(exit_code, 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["first_bot_message"], "# 摘要\n这是摘要译文。")
        self.assertEqual(result["continue_count_used"], 0)
        self.assertEqual(result["messages"][0]["message_kind"], "system_prompt")
        self.assertNotIn("[TRANSLATION_STATUS_JSON]", result["messages"][1]["content"])

    def test_bridge_rejects_missing_agent_output_json(self):
        exit_code, result = self._run_main_with_payload({})

        self.assertEqual(exit_code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_input")

    def test_bridge_rejects_malformed_bot_payload(self):
        artifact = {
            "messages": [
                {
                    "role": "user",
                    "content": "Translate ABSTRACT from the local PDF.",
                    "message_kind": "system_prompt",
                    "visible_to_user": False,
                },
                {
                    "role": "bot",
                    "content": "# 摘要\n这是摘要译文。",
                    "message_kind": "bot_reply",
                    "visible_to_user": True,
                    "client_payload": {},
                },
            ],
            "translation_plan": {
                "status": "ok",
                "units": ["ABSTRACT"],
                "appendix_units": [],
                "reason": "",
            },
            "translation_status": {
                "protocol": "unit_v1",
                "planner_status": "ok",
                "active_scope": "done",
                "active_units": [],
                "current_unit_id": "ABSTRACT",
                "current_unit_index": -1,
                "completed_unit_ids": ["ABSTRACT"],
                "remaining_unit_ids": [],
                "next_unit_id": "",
                "state": "ALL_DONE",
                "reason": "",
                "total_unit_count": 1,
                "completed_unit_count": 1,
                "source": "canonical_payload",
                "is_completed": True,
                "is_all_done": True,
            },
            "errors": [],
        }

        exit_code, result = self._run_main_with_payload({}, artifact)

        self.assertEqual(exit_code, 1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "invalid_output")


class SelfTranslateFullPaperSkillBuilderTest(unittest.TestCase):
    def test_build_artifact_materializes_messages_from_unit_results(self):
        request = {
            "mode": "build_artifact",
            "translation_plan": {
                "status": "ok",
                "units": ["ABSTRACT", "1 Introduction"],
                "appendix_units": [],
                "reason": "",
            },
            "unit_results": [
                {
                    "unit_id": "ABSTRACT",
                    "state": "OK",
                    "content": "# 摘要\n这是摘要译文。",
                },
                {
                    "unit_id": "1 Introduction",
                    "state": "OK",
                    "content": "# 1 引言\n这是引言译文。",
                },
            ],
            "errors": [],
        }

        artifact = self_translate_skill._materialize_agent_artifact(request)

        self.assertEqual(len(artifact["messages"]), 4)
        self.assertEqual(artifact["messages"][0]["message_kind"], "system_prompt")
        self.assertEqual(artifact["messages"][2]["message_kind"], "continue_command")
        self.assertEqual(artifact["first_bot_message"], "# 摘要\n这是摘要译文。")
        self.assertEqual(artifact["continue_count_used"], 1)
        self.assertEqual(artifact["translation_status"]["state"], "ALL_DONE")

    def test_build_artifact_stops_at_unsupported_unit(self):
        request = {
            "mode": "build_artifact",
            "translation_plan": {
                "status": "ok",
                "units": ["ABSTRACT", "2 Method", "3 Results"],
                "appendix_units": [],
                "reason": "",
            },
            "unit_results": [
                {
                    "unit_id": "ABSTRACT",
                    "state": "OK",
                    "content": "# 摘要\n这是摘要译文。",
                },
                {
                    "unit_id": "2 Method",
                    "state": "UNSUPPORTED",
                    "reason": "ambiguous_unit_boundary",
                },
            ],
            "errors": [],
        }

        artifact = self_translate_skill._materialize_agent_artifact(request)

        self.assertEqual(artifact["continue_count_used"], 1)
        self.assertEqual(artifact["translation_status"]["state"], "UNSUPPORTED")
        self.assertEqual(artifact["translation_status"]["current_unit_id"], "2 Method")
        self.assertEqual(artifact["messages"][-1]["content"], "")


class SelfTranslateFullPaperSkillExamplesTest(unittest.TestCase):
    def test_examples_cover_required_protocol_scenarios(self):
        examples_path = Path(__file__).resolve().parents[1] / "skills" / "self-translate-full-paper-skill" / "references" / "examples.md"
        text = examples_path.read_text(encoding="utf-8")
        sections = re.findall(r"## (.+?)\n\n```json\n(.*?)\n```", text, flags=re.DOTALL)

        self.assertEqual(len(sections), 4)

        expected_titles = {
            "Abstract And Body",
            "Body And Appendix Completion",
            "Unsupported Planner",
            "Partial Ambiguous Later Unit",
        }
        self.assertEqual({title for title, _ in sections}, expected_titles)

        for title, raw_json in sections:
            payload = json.loads(raw_json)
            normalized_plan = normalize_translation_plan_payload(payload.get("translation_plan"))
            normalized_status = normalize_translation_status_payload(payload.get("translation_status"))
            self.assertIsNotNone(normalized_plan, title)
            self.assertIsNotNone(normalized_status, title)

            normalized_payload = self_translate_skill._normalize_agent_result(payload)
            self.assertTrue(normalized_payload["ok"], title)
            self.assertEqual(normalized_payload["translation_plan"], normalized_plan, title)
            self.assertEqual(normalized_payload["translation_status"], normalized_status, title)

        by_title = {title: json.loads(raw_json) for title, raw_json in sections}
        self.assertEqual(by_title["Abstract And Body"]["translation_status"]["state"], "ALL_DONE")
        self.assertEqual(by_title["Body And Appendix Completion"]["translation_status"]["current_unit_id"], "APPENDIX A")
        self.assertEqual(by_title["Unsupported Planner"]["translation_status"]["state"], "UNSUPPORTED")
        self.assertEqual(by_title["Partial Ambiguous Later Unit"]["translation_status"]["reason"], "ambiguous_unit_boundary")


if __name__ == "__main__":
    unittest.main()
