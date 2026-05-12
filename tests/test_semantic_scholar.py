from __future__ import annotations

import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from backend.platform.gateways import semantic_scholar


class SemanticScholarGatewayTest(unittest.TestCase):
    def test_fetch_title_match_not_found_returns_empty_match_payload(self):
        error = HTTPError(
            url="https://api.semanticscholar.org/graph/v1/paper/search/match",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=BytesIO(b'{"error":"Title match not found"}'),
        )

        with patch.object(semantic_scholar.urllib.request, "urlopen", side_effect=error):
            payload = semantic_scholar.fetch_semantic_scholar_match("Unmatched Paper", max_retries=0)

        self.assertEqual(payload["data"], [])
        self.assertEqual(payload["status_code"], 404)
        self.assertIn("Title match not found", payload["error"])

    def test_build_result_payload_marks_empty_match_as_not_found(self):
        payload = semantic_scholar.build_result_payload(
            "conv-1",
            {
                "data": [],
                "error": '{"error":"Title match not found"}',
                "status_code": 404,
                "query": "Unmatched Paper",
            },
        )

        self.assertEqual(payload["conversation_id"], "conv-1")
        self.assertEqual(payload["status"], "not_found")
        self.assertIsNone(payload["matched_title"])
        self.assertIn("Title match not found", payload["raw_response_json"])


if __name__ == "__main__":
    unittest.main()
