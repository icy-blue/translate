from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
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

    def test_get_bot_response_converts_local_file_url_to_data_url(self):
        captured_payloads: list[dict] = []

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = Path(tmpdir) / "conv-1" / "file-1.pdf"
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(b"%PDF-local")

            def fake_urlopen(req, timeout):
                captured_payloads.append(json.loads(req.data.decode("utf-8")))
                return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

            attachment = fp.Attachment(
                url="/files/conv-1/file-1.pdf",
                content_type="application/pdf",
                name="paper.pdf",
            )
            message = fp.ProtocolMessage(role="user", content="Read this.", attachments=[attachment])

            with (
                patch("backend.platform.local_files.LOCAL_FILES_DIR", Path(tmpdir)),
                patch.object(poe.request, "urlopen", side_effect=fake_urlopen),
            ):
                response = asyncio.run(poe.get_bot_response([message], "Claude-Sonnet-4.6", "test-key"))

        self.assertEqual(response, "ok")
        file_data = captured_payloads[0]["messages"][0]["content"][1]["file"]["file_data"]
        self.assertTrue(file_data.startswith("data:application/pdf;base64,"))

    def test_get_bot_response_sends_deepseek_openai_compatible_text(self):
        captured_payloads: list[dict] = []

        def fake_urlopen(req, timeout):
            captured_payloads.append(json.loads(req.data.decode("utf-8")))
            self.assertEqual(req.full_url, "https://api.deepseek.com/chat/completions")
            self.assertEqual(req.headers["Authorization"], "Bearer deepseek-key")
            return _FakeResponse({"choices": [{"message": {"content": "deepseek ok"}}]})

        attachment = fp.Attachment(
            url="data:application/pdf;base64,JVBERg==",
            content_type="application/pdf",
            name="paper.pdf",
        )
        message = fp.ProtocolMessage(role="user", content="Read this.", attachments=[attachment])

        with (
            patch.object(poe, "_pdf_text_from_bytes", return_value="Extracted PDF text."),
            patch.object(poe.request, "urlopen", side_effect=fake_urlopen),
        ):
            response = asyncio.run(poe.get_bot_response([message], "deepseek-v4-pro", "deepseek-key", provider="deepseek"))

        self.assertEqual(response, "deepseek ok")
        self.assertEqual(captured_payloads[0]["model"], "deepseek-v4-pro")
        content = captured_payloads[0]["messages"][0]["content"]
        self.assertIsInstance(content, str)
        self.assertIn("Read this.", content)
        self.assertIn("[PDF: paper.pdf]", content)
        self.assertIn("Extracted PDF text.", content)

    def test_deepseek_arxiv_pdf_uses_html_text_without_pdf_extraction(self):
        captured_payloads: list[dict] = []

        def fake_urlopen(req, timeout):
            captured_payloads.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

        attachment = fp.Attachment(
            url="data:application/pdf;base64,JVBERg==",
            content_type="application/pdf",
            name="2605.10922v1.pdf",
        )
        message = fp.ProtocolMessage(role="user", content="Plan this.", attachments=[attachment])

        with (
            patch.object(poe, "_arxiv_html_text", return_value="Precise arXiv HTML text.") as html_mock,
            patch.object(poe, "_pdf_text_from_bytes", side_effect=AssertionError("PDF extraction should not be used")),
            patch.object(poe.request, "urlopen", side_effect=fake_urlopen),
        ):
            response = asyncio.run(poe.get_bot_response([message], "deepseek-v4-pro", "deepseek-key", provider="deepseek"))

        self.assertEqual(response, "ok")
        html_mock.assert_called_once_with("2605.10922v1")
        content = captured_payloads[0]["messages"][0]["content"]
        self.assertIn("[arXiv HTML: 2605.10922v1]", content)
        self.assertIn("Precise arXiv HTML text.", content)


if __name__ == "__main__":
    unittest.main()
