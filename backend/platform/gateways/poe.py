from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from typing import Any
from typing import Optional
from urllib import error, request

import fastapi_poe as fp

from ...domain.paper_tags import (
    build_category_selection_prompt,
    build_tag_payloads,
    build_tagging_followup_prompt,
    parse_category_codes,
    parse_tag_codes,
)
from ..config import settings

POE_OPENAI_BASE_URL = "https://api.poe.com/v1"
POE_CHAT_COMPLETIONS_URL = f"{POE_OPENAI_BASE_URL}/chat/completions"


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
    req = request.Request(url, headers={"User-Agent": "translate-poe-gateway/1.0", "Accept": f"{content_type},*/*"})
    with request.urlopen(req, timeout=120) as response:
        return _to_data_url(response.read(), content_type)


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


def _post_chat_completion(payload: dict[str, Any], api_key: str) -> str:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        POE_CHAT_COMPLETIONS_URL,
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
        raise RuntimeError(f"Poe API request failed with HTTP {exc.code}: {error_body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Poe API request failed: {exc}") from exc

    try:
        return str(data["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Poe API response: {data}") from exc


def _get_chat_completion(messages: list[fp.ProtocolMessage], bot_name: str, api_key: str) -> str:
    payload = {
        "model": bot_name,
        "messages": [_message_payload(message) for message in messages],
    }
    return _post_chat_completion(payload, api_key)


async def extract_title_from_pdf(pdf_attachment: fp.Attachment, api_key: str, model: str) -> Optional[str]:
    prompt = settings.title_prompt
    message = fp.ProtocolMessage(role="user", content=prompt, attachments=[pdf_attachment])
    title_text = await get_bot_response([message], model, api_key)
    return title_text.strip() or None


async def get_bot_response(messages: list[fp.ProtocolMessage], bot_name: str, api_key: str) -> str:
    return await asyncio.to_thread(_get_chat_completion, messages, bot_name, api_key)


async def upload_file(file, api_key: str, file_name: str) -> fp.Attachment:
    content = file.read()
    if isinstance(content, str):
        content = content.encode("utf-8")
    content_type = _guess_content_type(file_name)
    return fp.Attachment(url=_to_data_url(content, content_type), content_type=content_type, name=file_name)


async def classify_paper_tags(title: str, abstract: str, bot_name: str, api_key: str) -> list[dict]:
    stage1_prompt = build_category_selection_prompt(title, abstract)
    stage1_message = fp.ProtocolMessage(role="user", content=stage1_prompt)
    stage1_response = await get_bot_response([stage1_message], bot_name, api_key)

    category_codes = parse_category_codes(stage1_response)
    stage2_prompt = build_tagging_followup_prompt(category_codes)
    stage2_messages = [
        stage1_message,
        fp.ProtocolMessage(role="bot", content=stage1_response),
        fp.ProtocolMessage(role="user", content=stage2_prompt),
    ]
    stage2_response = await get_bot_response(stage2_messages, bot_name, api_key)
    return build_tag_payloads(parse_tag_codes(stage2_response), source="poe")
