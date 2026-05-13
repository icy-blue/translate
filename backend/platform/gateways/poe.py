from __future__ import annotations

import asyncio
import base64
from html.parser import HTMLParser
import io
import json
import mimetypes
import re
from typing import Any
from typing import Optional
from urllib import error, request

import fastapi_poe as fp
from pypdf import PdfReader

from ...domain.paper_tags import (
    build_category_selection_prompt,
    build_tag_payloads,
    build_tagging_followup_prompt,
    parse_category_codes,
    parse_tag_codes,
)
from ..config import settings
from ..local_files import local_file_url_to_path

POE_OPENAI_BASE_URL = "https://api.poe.com/v1"
POE_CHAT_COMPLETIONS_URL = f"{POE_OPENAI_BASE_URL}/chat/completions"
DEEPSEEK_PROVIDER = "deepseek"
POE_PROVIDER = "poe"
MIXED_PROVIDER = "mixed"
SUPPORTED_PROVIDERS = {POE_PROVIDER, DEEPSEEK_PROVIDER, MIXED_PROVIDER}
ARXIV_ID_PATTERN = re.compile(r"(?i)(?:arxiv[:_\-\s/]+|abs/|pdf/|html/)?(\d{4}\.\d{4,5})(v\d+)?")


def normalize_provider(provider: str | None) -> str:
    normalized = str(provider or POE_PROVIDER).strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    return normalized


def _guess_content_type(file_name: str) -> str:
    return mimetypes.guess_type(file_name)[0] or "application/octet-stream"


def _to_data_url(content: bytes, content_type: str) -> str:
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _message_role(role: str) -> str:
    if role == "bot":
        return "assistant"
    if role in {"system", "assistant", "user", "tool"}:
        return role
    return "user"


def _is_image_content_type(content_type: str) -> bool:
    return content_type.lower().startswith("image/")


def _file_data_url(url: str, content_type: str) -> str:
    if url.startswith("data:"):
        return url
    local_path = local_file_url_to_path(url)
    if local_path is not None:
        return _to_data_url(local_path.read_bytes(), content_type)
    req = request.Request(url, headers={"User-Agent": "translate-poe-gateway/1.0", "Accept": f"{content_type},*/*"})
    with request.urlopen(req, timeout=120) as response:
        return _to_data_url(response.read(), content_type)


def _file_bytes(url: str, content_type: str) -> bytes:
    if url.startswith("data:"):
        header, _, encoded = url.partition(",")
        if ";base64" not in header:
            raise RuntimeError("Unsupported data URL attachment encoding.")
        return base64.b64decode(encoded)
    local_path = local_file_url_to_path(url)
    if local_path is not None:
        return local_path.read_bytes()
    req = request.Request(url, headers={"User-Agent": "translate-poe-gateway/1.0", "Accept": f"{content_type},*/*"})
    with request.urlopen(req, timeout=120) as response:
        return response.read()


def _pdf_text_from_bytes(content: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(content))
        page_text = [(page.extract_text() or "").strip() for page in reader.pages]
    except Exception as exc:
        raise RuntimeError(f"Failed to extract PDF text for DeepSeek: {exc}") from exc
    text = "\n\n".join(part for part in page_text if part)
    if not text.strip():
        raise RuntimeError("DeepSeek provider requires extractable PDF text; this PDF may be scanned or image-based.")
    return text


def extract_arxiv_id(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            candidates = [json.dumps(value, ensure_ascii=False)]
        else:
            candidates = [str(value)]
        for candidate in candidates:
            for match in ARXIV_ID_PATTERN.finditer(candidate):
                prefix = candidate[max(0, match.start() - 12) : match.start()].lower()
                raw_match = match.group(0).lower()
                if "semanticscholar" in prefix and not any(marker in raw_match for marker in ("arxiv", "abs/", "pdf/", "html/")):
                    continue
                return f"{match.group(1)}{match.group(2) or ''}"
    return None


def extract_arxiv_id_from_pdf_bytes(content: bytes) -> str | None:
    try:
        return extract_arxiv_id(_pdf_text_from_bytes(content))
    except Exception:
        return None


class _ArxivHtmlTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "math"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg", "math"} and self._skip_depth:
            self._skip_depth -= 1
        if tag.lower() in {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "tr"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._chunks)).strip()


def _arxiv_html_url(arxiv_id: str) -> str:
    return f"https://arxiv.org/html/{arxiv_id}"


def _arxiv_html_text(arxiv_id: str) -> str:
    req = request.Request(
        _arxiv_html_url(arxiv_id),
        headers={"User-Agent": "translate-deepseek-arxiv-html/1.0", "Accept": "text/html,*/*"},
    )
    try:
        with request.urlopen(req, timeout=120) as response:
            html = response.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Failed to fetch arXiv HTML {arxiv_id} with HTTP {exc.code}: {body[:500]}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Failed to fetch arXiv HTML {arxiv_id}: {exc}") from exc

    parser = _ArxivHtmlTextParser()
    parser.feed(html)
    text = parser.text()
    if not text:
        raise RuntimeError(f"arXiv HTML {arxiv_id} did not contain extractable text.")
    return text


def _attachment_part(attachment: Any) -> dict[str, Any]:
    url = str(getattr(attachment, "url", "")).strip()
    content_type = str(getattr(attachment, "content_type", "")).strip() or "application/octet-stream"
    name = str(getattr(attachment, "name", "")).strip() or "attachment"
    if _is_image_content_type(content_type):
        return {"type": "image_url", "image_url": {"url": url}}
    return {
        "type": "file",
        "file": {
            "filename": name,
            "file_data": _file_data_url(url, content_type),
        },
    }


def _message_payload(message: fp.ProtocolMessage) -> dict[str, Any]:
    attachments = list(getattr(message, "attachments", None) or [])
    content = str(getattr(message, "content", "") or "")
    if not attachments:
        return {"role": _message_role(str(message.role)), "content": content}

    parts: list[dict[str, Any]] = []
    if content:
        parts.append({"type": "text", "text": content})
    parts.extend(_attachment_part(attachment) for attachment in attachments)
    return {"role": _message_role(str(message.role)), "content": parts}


def _deepseek_attachment_text(attachment: Any, arxiv_id: str | None = None) -> str:
    url = str(getattr(attachment, "url", "")).strip()
    content_type = str(getattr(attachment, "content_type", "")).strip() or "application/octet-stream"
    name = str(getattr(attachment, "name", "")).strip() or "attachment"
    if content_type.lower() == "application/pdf" or name.lower().endswith(".pdf"):
        detected_arxiv_id = arxiv_id or extract_arxiv_id(name, url)
        if detected_arxiv_id:
            text = _arxiv_html_text(detected_arxiv_id)
            return f"[arXiv HTML: {detected_arxiv_id}]\n{text}"
        text = _pdf_text_from_bytes(_file_bytes(url, content_type))
        return f"[PDF: {name}]\n{text}"
    if _is_image_content_type(content_type):
        raise RuntimeError("DeepSeek provider does not support image attachments in this workflow.")
    content = _file_bytes(url, content_type).decode("utf-8", errors="replace").strip()
    if not content:
        raise RuntimeError(f"DeepSeek provider could not read text from attachment: {name}")
    return f"[Attachment: {name}]\n{content}"


def _deepseek_message_payload(message: fp.ProtocolMessage, arxiv_id: str | None = None) -> dict[str, Any]:
    attachments = list(getattr(message, "attachments", None) or [])
    content = str(getattr(message, "content", "") or "").strip()
    if attachments:
        attachment_blocks = "\n\n".join(_deepseek_attachment_text(attachment, arxiv_id=arxiv_id) for attachment in attachments)
        content = f"{content}\n\n{attachment_blocks}" if content else attachment_blocks
    return {"role": _message_role(str(message.role)), "content": content}


def _post_chat_completion(payload: dict[str, Any], api_key: str, *, provider: str) -> str:
    normalized_provider = normalize_provider(provider)
    if normalized_provider == MIXED_PROVIDER:
        raise RuntimeError("Mixed provider must be resolved before gateway calls.")
    if normalized_provider == DEEPSEEK_PROVIDER:
        url = f"{settings.deepseek_base_url.rstrip('/')}/chat/completions"
        provider_label = "DeepSeek"
    else:
        url = POE_CHAT_COMPLETIONS_URL
        provider_label = "Poe"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=600) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{provider_label} API request failed with HTTP {exc.code}: {error_body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"{provider_label} API request failed: {exc}") from exc

    try:
        return str(data["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected {provider_label} API response: {data}") from exc


def _get_chat_completion(
    messages: list[fp.ProtocolMessage],
    bot_name: str,
    api_key: str,
    provider: str = POE_PROVIDER,
    arxiv_id: str | None = None,
) -> str:
    normalized_provider = normalize_provider(provider)
    payload = {
        "model": bot_name,
        "messages": [
            _deepseek_message_payload(message, arxiv_id=arxiv_id) if normalized_provider == DEEPSEEK_PROVIDER else _message_payload(message)
            for message in messages
        ],
    }
    return _post_chat_completion(payload, api_key, provider=normalized_provider)


async def extract_title_from_pdf(
    pdf_attachment: fp.Attachment,
    api_key: str,
    model: str,
    provider: str = POE_PROVIDER,
    arxiv_id: str | None = None,
) -> Optional[str]:
    prompt = settings.title_prompt
    message = fp.ProtocolMessage(role="user", content=prompt, attachments=[pdf_attachment])
    title_text = await get_bot_response([message], model, api_key, provider=provider, arxiv_id=arxiv_id)
    return title_text.strip() or None


async def get_bot_response(
    messages: list[fp.ProtocolMessage],
    bot_name: str,
    api_key: str,
    provider: str = POE_PROVIDER,
    arxiv_id: str | None = None,
) -> str:
    return await asyncio.to_thread(_get_chat_completion, messages, bot_name, api_key, provider, arxiv_id)


async def upload_file(file, api_key: str, file_name: str) -> fp.Attachment:
    content = file.read()
    if isinstance(content, str):
        content = content.encode("utf-8")
    content_type = _guess_content_type(file_name)
    return fp.Attachment(url=_to_data_url(content, content_type), content_type=content_type, name=file_name)


async def classify_paper_tags(title: str, abstract: str, bot_name: str, api_key: str, provider: str = POE_PROVIDER) -> list[dict]:
    stage1_prompt = build_category_selection_prompt(title, abstract)
    stage1_message = fp.ProtocolMessage(role="user", content=stage1_prompt)
    stage1_response = await get_bot_response([stage1_message], bot_name, api_key, provider=provider)

    category_codes = parse_category_codes(stage1_response)
    stage2_prompt = build_tagging_followup_prompt(category_codes)
    stage2_messages = [
        stage1_message,
        fp.ProtocolMessage(role="bot", content=stage1_response),
        fp.ProtocolMessage(role="user", content=stage2_prompt),
    ]
    stage2_response = await get_bot_response(stage2_messages, bot_name, api_key, provider=provider)
    return build_tag_payloads(parse_tag_codes(stage2_response), source=normalize_provider(provider))
