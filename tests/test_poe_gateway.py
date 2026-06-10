from __future__ import annotations

import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fastapi_poe as fp
import fitz

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
    def test_normalize_provider_accepts_mixed(self):
        self.assertEqual(poe.normalize_provider("mixed"), "mixed")

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

    def test_get_bot_response_filters_deepseek_pdf_artifacts(self):
        captured_payloads: list[dict] = []

        def fake_urlopen(req, timeout):
            captured_payloads.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse({"choices": [{"message": {"content": "deepseek ok"}}]})

        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "1 Introduction")
        page.insert_text((72, 100), "Figure 1: Overview of the method.")
        page.insert_text((72, 128), "Figure 1 shows the model pipeline.")
        page.insert_text((72, 156), "Table 1: Quantitative comparison.")
        page.insert_text((72, 184), "Method   Accuracy   Runtime")
        page.insert_text((72, 212), "Ours     95.2       1.0")
        pdf_bytes = document.tobytes()
        document.close()

        attachment = fp.Attachment(
            url="data:application/pdf;base64,JVBERg==",
            content_type="application/pdf",
            name="paper.pdf",
        )
        message = fp.ProtocolMessage(role="user", content="Translate this.", attachments=[attachment])

        with (
            patch.object(poe, "_file_bytes", return_value=pdf_bytes),
            patch.object(poe.request, "urlopen", side_effect=fake_urlopen),
        ):
            response = asyncio.run(poe.get_bot_response([message], "deepseek-v4-pro", "deepseek-key", provider="deepseek"))

        self.assertEqual(response, "deepseek ok")
        content = captured_payloads[0]["messages"][0]["content"]
        self.assertIn("1 Introduction", content)
        self.assertIn("Figure 1 shows the model pipeline.", content)
        self.assertNotIn("Overview of the method", content)
        self.assertNotIn("Quantitative comparison", content)
        self.assertNotIn("Method Accuracy Runtime", content)
        self.assertNotIn("Ours 95.2", content)

    def test_deepseek_arxiv_pdf_uses_pdf_text(self):
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
            patch.object(poe, "_pdf_text_from_bytes", return_value="Extracted arXiv PDF text."),
            patch.object(poe.request, "urlopen", side_effect=fake_urlopen),
        ):
            response = asyncio.run(poe.get_bot_response([message], "deepseek-v4-pro", "deepseek-key", provider="deepseek"))

        self.assertEqual(response, "ok")
        content = captured_payloads[0]["messages"][0]["content"]
        self.assertIn("[PDF: 2605.10922v1.pdf]", content)
        self.assertIn("Extracted arXiv PDF text.", content)
        self.assertNotIn("arXiv HTML", content)

    def test_deepseek_second_arxiv_pdf_uses_pdf_text(self):
        captured_payloads: list[dict] = []

        def fake_urlopen(req, timeout):
            captured_payloads.append(json.loads(req.data.decode("utf-8")))
            return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

        attachment = fp.Attachment(
            url="data:application/pdf;base64,JVBERg==",
            content_type="application/pdf",
            name="2311.10709v2.pdf",
        )
        message = fp.ProtocolMessage(role="user", content="Translate section 3.", attachments=[attachment])

        with (
            patch.object(poe, "_pdf_text_from_bytes", return_value="Extracted full PDF text with 3 Approach."),
            patch.object(poe.request, "urlopen", side_effect=fake_urlopen),
        ):
            response = asyncio.run(poe.get_bot_response([message], "deepseek-v4-pro", "deepseek-key", provider="deepseek"))

        self.assertEqual(response, "ok")
        content = captured_payloads[0]["messages"][0]["content"]
        self.assertIn("[PDF: 2311.10709v2.pdf]", content)
        self.assertIn("Extracted full PDF text with 3 Approach.", content)
        self.assertNotIn("[arXiv HTML: 2311.10709v2]", content)


if __name__ == "__main__":
    unittest.main()
