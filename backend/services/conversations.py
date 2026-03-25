from __future__ import annotations

import fastapi_poe as fp
from fastapi import HTTPException
from sqlmodel import Session

from ..core.config import settings
from ..integrations.poe import get_bot_response
from ..persistence import crud
from .message_utils import (
    infer_message_metadata,
    normalize_document_outline_payload,
    normalize_translation_status_payload,
    preprocess_bot_reply_for_storage,
    safe_json_loads,
)
from .translation_prompts import build_continue_translation_prompt


def _prepare_bot_response(response_text: str) -> dict:
    prepared_response = preprocess_bot_reply_for_storage(response_text)
    response_content = str(prepared_response["content"])
    return {
        "reply": response_text,
        "content": response_content,
        "display_reply": response_content,
        "section_category": None,
        "translation_status": prepared_response["translation_status"],
        "document_outline": prepared_response["document_outline"],
        "client_payload": prepared_response["client_payload"],
    }


def _get_latest_translation_context(session: Session, conversation_id: str) -> dict[str, dict]:
    messages = crud.get_messages(session, conversation_id)
    for message in reversed(messages):
        if infer_message_metadata(message)["role"] != "bot":
            continue
        payload = safe_json_loads(message.client_payload_json, {})
        if not isinstance(payload, dict):
            continue
        translation_status = normalize_translation_status_payload(payload.get("translation_status"))
        if translation_status is None:
            continue
        document_outline = normalize_document_outline_payload(payload.get("document_outline"))
        return {
            "translation_status": translation_status,
            "document_outline": document_outline,
        }
    raise HTTPException(
        status_code=409,
        detail="会话缺少可用的 translation_status，无法使用无状态续翻。请先完成 payload backfill。",
    )


async def translate_conversation_stateless(
    conversation_id: str,
    action: str,
    target_scope: str,
    poe_model: str,
    api_key: str,
    session: Session,
    progress_callback=None,
):
    if progress_callback:
        progress_callback("校验会话与文件")
    conversation = crud.get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    file_record = crud.get_file_record(session, conversation_id)
    if not file_record:
        raise HTTPException(status_code=404, detail="File record not found.")

    if progress_callback:
        progress_callback("读取最新翻译状态")
    latest_context = _get_latest_translation_context(session, conversation_id)
    translation_status = latest_context["translation_status"]

    if progress_callback:
        progress_callback("构建无状态续翻请求")
    prompt = build_continue_translation_prompt(
        settings.continue_prompt,
        translation_status=translation_status,
        action=action,
        target_scope=target_scope,
    )
    pdf_attachment = fp.Attachment(url=file_record.poe_url, content_type=file_record.content_type, name=file_record.poe_name)

    if progress_callback:
        progress_callback("等待 Poe 返回翻译结果")
    response_text = await get_bot_response(
        [fp.ProtocolMessage(role="user", content=prompt, attachments=[pdf_attachment])],
        poe_model,
        api_key,
    )
    prepared_response = _prepare_bot_response(response_text)

    if progress_callback:
        progress_callback("写入会话消息到数据库")
    crud.create_messages(
        session,
        conversation_id,
        prompt,
        response_text,
        user_message_kind="continue_command",
        user_visible_to_user=False,
        bot_section_category=prepared_response["section_category"],
        bot_client_payload=prepared_response["client_payload"],
    )

    if progress_callback:
        progress_callback("翻译结果已生成")
    return {
        "reply": prepared_response["reply"],
        "content": prepared_response["content"],
        "display_reply": prepared_response["display_reply"],
        "section_category": prepared_response["section_category"],
        "translation_status": prepared_response["translation_status"],
        "document_outline": prepared_response["document_outline"],
    }
