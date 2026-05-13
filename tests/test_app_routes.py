from __future__ import annotations

import unittest
from pathlib import Path

from app import app


class AppRoutesTest(unittest.TestCase):
    def test_new_task_oriented_routes_are_registered(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/tasks/ingest-pdf", paths)
        self.assertIn("/tasks/{task_id}", paths)
        self.assertIn("/translations/{conversation_id}/continue", paths)
        self.assertIn("/translations/{conversation_id}/glossary", paths)
        self.assertIn("/conversations/{conversation_id}", paths)
        self.assertIn("/metadata/{conversation_id}/refresh", paths)
        self.assertIn("/metadata/{conversation_id}/tags", paths)
        self.assertIn("/assets/{conversation_id}/reprocess", paths)
        self.assertIn("/pipeline/commits", paths)
        self.assertIn("/files", paths)

    def test_conversation_delete_route_is_registered(self):
        routes = {(route.path, tuple(sorted(getattr(route, "methods", [])))) for route in app.routes}
        self.assertIn(("/conversations/{conversation_id}", ("DELETE",)), routes)

    def test_legacy_routes_are_not_registered(self):
        paths = {route.path for route in app.routes}
        self.assertNotIn("/upload", paths)
        self.assertNotIn("/jobs/{job_id}", paths)
        self.assertNotIn("/conversation/{conversation_id}", paths)
        self.assertNotIn("/conversation/{conversation_id}/translate", paths)
        self.assertNotIn("/conversation/{conversation_id}/refresh_metadata", paths)
        self.assertNotIn("/conversation/{conversation_id}/reprocess_assets", paths)
        self.assertNotIn("/agent/pipeline/commit", paths)

    def test_upload_form_includes_tag_model(self):
        html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('formData.append("tag_model", activeExtractionModel);', html)
        self.assertIn('{ label: "混合", value: "mixed" }', html)
        self.assertIn('formData.append("poe_api_key", poeApiKey);', html)
        self.assertIn('formData.append("deepseek_api_key", deepseekApiKey);', html)

    def test_chat_delete_ui_requires_desktop_confirmation(self):
        html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("DeleteOutlined", html)
        self.assertIn('method: "DELETE"', html)
        self.assertIn("confirmation_id: deleteConfirmationId", html)
        self.assertIn("!readOnly && !isMobile && conversationId", html)

    def test_running_task_hint_waits_before_showing(self):
        html = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const runningHintDelayMs = 2 * 60 * 1000;", html)
        self.assertIn("如果长时间运行中，可重启后端，任务会自动重新执行。", html)
        self.assertIn('data.status === "running"', html)


if __name__ == "__main__":
    unittest.main()
