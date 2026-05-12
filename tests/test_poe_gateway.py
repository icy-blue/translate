from __future__ import annotations

import asyncio
import io
import json
import unittest
from unittest.mock import patch

import fastapi_poe as fp

from backend.platform.gateways import poe


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _FakeResponseBytes:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


class PoeGatewayTest(unittest.TestCase):
    def test_upload_file_returns_pdf_data_url_attachment(self):
        attachment = asyncio.run(poe.upload_file(io.BytesIO(b"%PDF-test"), "unused-key", "paper.pdf"))

        self.assertEqual(attachment.content_type, "application/pdf")
        self.assertEqual(attachment.name, "paper.pdf")
        self.assertTrue(attachment.url.startswith("data:application/pdf;base64,"))

    def test_get_bot_response_sends_openai_compatible_file_part(self):
        captured_payloads: list[dict] = []

        def fake_urlopen(req, timeout):
            captured_payloads.append(json.loads(req.data.decode("utf-8")))
            self.assertEqual(req.full_url, poe.POE_CHAT_COMPLETIONS_URL)
            self.assertEqual(req.headers["Authorization"], "Bearer test-key")
            return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

        attachment = fp.Attachment(
            url="data:application/pdf;base64,JVBERg==",
            content_type="application/pdf",
            name="paper.pdf",
        )
        message = fp.ProtocolMessage(role="user", content="Read this.", attachments=[attachment])

        with patch.object(poe.request, "urlopen", side_effect=fake_urlopen):
            response = asyncio.run(poe.get_bot_response([message], "Claude-Sonnet-4.6", "test-key"))

        self.assertEqual(response, "ok")
        content = captured_payloads[0]["messages"][0]["content"]
        self.assertEqual(captured_payloads[0]["model"], "Claude-Sonnet-4.6")
        self.assertEqual(content[0], {"type": "text", "text": "Read this."})
        self.assertEqual(
            content[1],
            {
                "type": "file",
                "file": {
                    "filename": "paper.pdf",
                    "file_data": "data:application/pdf;base64,JVBERg==",
                },
            },
        )

    def test_get_bot_response_converts_legacy_file_url_to_data_url(self):
        captured_payloads: list[dict] = []

        def fake_urlopen(req, timeout):
            if req.full_url == "https://example.invalid/paper.pdf":
                return _FakeResponseBytes(b"%PDF-legacy")
            captured_payloads.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

        attachment = fp.Attachment(
            url="https://example.invalid/paper.pdf",
            content_type="application/pdf",
            name="paper.pdf",
        )
        message = fp.ProtocolMessage(role="user", content="Read this.", attachments=[attachment])

        with patch.object(poe.request, "urlopen", side_effect=fake_urlopen):
            response = asyncio.run(poe.get_bot_response([message], "Claude-Sonnet-4.6", "test-key"))

        self.assertEqual(response, "ok")
        file_data = captured_payloads[0]["messages"][0]["content"][1]["file"]["file_data"]
        self.assertTrue(file_data.startswith("data:application/pdf;base64,"))


if __name__ == "__main__":
    unittest.main()
