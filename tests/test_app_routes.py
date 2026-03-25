from __future__ import annotations

import unittest

from app import app


class AppRoutesTest(unittest.TestCase):
    def test_new_task_oriented_routes_are_registered(self):
        paths = {route.path for route in app.routes}
        self.assertIn("/tasks/ingest-pdf", paths)
        self.assertIn("/tasks/{task_id}", paths)
        self.assertIn("/translations/{conversation_id}/continue", paths)
        self.assertIn("/conversations/{conversation_id}", paths)
        self.assertIn("/metadata/{conversation_id}/refresh", paths)
        self.assertIn("/metadata/{conversation_id}/tags", paths)
        self.assertIn("/assets/{conversation_id}/reprocess", paths)
        self.assertIn("/pipeline/commits", paths)

    def test_legacy_routes_are_not_registered(self):
        paths = {route.path for route in app.routes}
        self.assertNotIn("/upload", paths)
        self.assertNotIn("/jobs/{job_id}", paths)
        self.assertNotIn("/conversation/{conversation_id}", paths)
        self.assertNotIn("/conversation/{conversation_id}/translate", paths)
        self.assertNotIn("/conversation/{conversation_id}/refresh_metadata", paths)
        self.assertNotIn("/conversation/{conversation_id}/reprocess_assets", paths)
        self.assertNotIn("/agent/pipeline/commit", paths)


if __name__ == "__main__":
    unittest.main()
