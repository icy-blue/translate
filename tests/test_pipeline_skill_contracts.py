from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


def _load_module(relative_path: str, module_name: str):
    module_path = Path(__file__).resolve().parents[1] / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose_bundle_skill = _load_module(
    "skills/compose-pipeline-bundle-skill/scripts/run.py",
    "compose_pipeline_bundle_skill_run",
)
persist_bundle_skill = _load_module(
    "skills/persist-pipeline-bundle-skill/scripts/run.py",
    "persist_pipeline_bundle_skill_run",
)


class ComposePipelineBundleSkillTest(unittest.TestCase):
    def test_normalize_file_record_requires_backend_mandatory_fields(self):
        self.assertIsNone(compose_bundle_skill._normalize_file_record({"filename": "paper.pdf"}))
        self.assertIsNone(
            compose_bundle_skill._normalize_file_record(
                {
                    "filename": "paper.pdf",
                    "fingerprint": "sha256",
                }
            )
        )

        normalized = compose_bundle_skill._normalize_file_record(
            {
                "filename": "paper.pdf",
                "fingerprint": "sha256",
                "poe_url": "https://example.invalid/paper.pdf",
            }
        )
        self.assertEqual(normalized["content_type"], "application/pdf")
        self.assertEqual(normalized["poe_name"], "paper.pdf")


class PersistPipelineBundleSkillTest(unittest.TestCase):
    def test_build_endpoint_targets_plural_pipeline_commits_route(self):
        endpoint = persist_bundle_skill._build_endpoint("http://localhost:8000/")
        self.assertEqual(endpoint, "http://localhost:8000/agent/pipeline/commits")

    def test_main_posts_bundle_to_plural_pipeline_commits_route(self):
        request_urls: list[str] = []

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps({"status": "succeeded"}).encode("utf-8")

        def _fake_urlopen(request, timeout=120):
            request_urls.append(request.full_url)
            return _FakeResponse()

        payload = {
            "base_url": "http://localhost:8000",
            "agent_token": "agent-token",
            "bundle": {
                "title": "Paper",
                "file_record": {
                    "filename": "paper.pdf",
                    "fingerprint": "sha256",
                    "poe_url": "https://example.invalid/paper.pdf",
                },
                "messages": [],
            },
        }

        with tempfile.NamedTemporaryFile(suffix=".json") as input_fp, tempfile.NamedTemporaryFile(suffix=".json") as output_fp:
            Path(input_fp.name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            with patch.object(persist_bundle_skill.urllib.request, "urlopen", side_effect=_fake_urlopen):
                with patch("sys.argv", ["run.py", "--input-json", input_fp.name, "--output-json", output_fp.name]):
                    exit_code = persist_bundle_skill.main()
            self.assertEqual(exit_code, 0)
            self.assertEqual(request_urls, ["http://localhost:8000/agent/pipeline/commits"])


if __name__ == "__main__":
    unittest.main()
