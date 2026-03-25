from __future__ import annotations

import fastapi_poe as fp

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..app.dependencies import check_read_only, get_api_key, get_db_session
from ..domain.message_payloads import (
    build_continue_translation_prompt,
    infer_message_metadata,
    normalize_translation_status_payload,
    preprocess_bot_reply_for_storage,
    safe_json_loads,
)
from ..platform.config import engine, settings
from ..platform.gateways.poe import get_bot_response
from ..platform.task_runtime import enqueue_task, get_active_task, get_session_enqueue_lock, mark_task_progress, register_task_definition
from .conversations import create_message_pair, get_conversation, get_file_record, get_messages

router = APIRouter(tags=["translation"])


class ContinueTranslationTaskPayload(BaseModel):
    conversation_id: str
    action: str = "continue"
    target_scope: str = "body"
    poe_model: str
    api_key: str


def _prepare_bot_response(response_text: str) -> dict:
    prepared_response = preprocess_bot_reply_for_storage(response_text)
    response_content = str(prepared_response["content"])
    return {
        "reply": response_text,
        "content": response_content,
        "display_reply": response_content,
        "section_category": None,
        "translation_status": prepared_response["translation_status"],
        "client_payload": prepared_response["client_payload"],
    }


def _get_latest_translation_context(session: Session, conversation_id: str) -> dict[str, object]:
    messages = get_messages(session, conversation_id)
    for message in reversed(messages):
        if infer_message_metadata(message)["role"] != "bot":
            continue
        payload = safe_json_loads(message.client_payload_json, {})
        if not isinstance(payload, dict):
            continue
        translation_status = normalize_translation_status_payload(payload.get("translation_status"))
        if translation_status is None:
            continue
        return {"translation_status": translation_status}
    raise HTTPException(
        status_code=409,
        detail="会话缺少可用的 translation_status，无法继续无状态续翻。请先完成 payload backfill。",
    )


async def queue_continue_translation(
    *,
    conversation_id: str,
    action: str,
    target_scope: str,
    poe_model: str,
    api_key: str,
    session: Session,
) -> dict:
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    enqueue_lock = await get_session_enqueue_lock(conversation_id)
    async with enqueue_lock:
        active_task = get_active_task(session, conversation_id, ["continue_translation"])
        if active_task:
            raise HTTPException(
                status_code=409,
                detail=f"会话已有翻译任务进行中（task_id={active_task.id}，状态={active_task.status}）。请等待完成后再继续。",
            )
        payload = ContinueTranslationTaskPayload(
            conversation_id=conversation_id,
            action=action,
            target_scope=target_scope,
            poe_model=poe_model,
            api_key=api_key,
        )
        return enqueue_task("continue_translation", payload, conversation_id=conversation_id)


async def handle_continue_translation(task_id: str, payload: ContinueTranslationTaskPayload) -> dict:
    if not payload.conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required.")
    if not payload.api_key:
        raise HTTPException(status_code=400, detail="API Key is required.")
    if payload.action != "continue":
        raise HTTPException(status_code=400, detail="Only action=continue is supported.")
    if payload.target_scope not in {"body", "appendix", "acknowledgements", "references"}:
        raise HTTPException(status_code=400, detail="Unsupported target_scope.")

    with Session(engine) as session:
        mark_task_progress(task_id, "校验会话与文件")
        conversation = get_conversation(session, payload.conversation_id)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        file_record = get_file_record(session, payload.conversation_id)
        if not file_record:
            raise HTTPException(status_code=404, detail="File record not found.")
        mark_task_progress(task_id, "读取最新翻译状态")
        latest_context = _get_latest_translation_context(session, payload.conversation_id)
        prompt = build_continue_translation_prompt(
            settings.continue_prompt,
            translation_status=latest_context["translation_status"],
            action=payload.action,
            target_scope=payload.target_scope,
        )
        pdf_attachment = fp.Attachment(url=file_record.poe_url, content_type=file_record.content_type, name=file_record.poe_name)
        mark_task_progress(task_id, "等待 Poe 返回翻译结果")
        response_text = await get_bot_response(
            [fp.ProtocolMessage(role="user", content=prompt, attachments=[pdf_attachment])],
            payload.poe_model,
            payload.api_key,
        )
        prepared_response = _prepare_bot_response(response_text)
        mark_task_progress(task_id, "写入会话消息")
        create_message_pair(
            session,
            payload.conversation_id,
            prompt,
            response_text,
            user_message_kind="continue_command",
            user_visible_to_user=False,
            bot_section_category=prepared_response["section_category"],
            bot_client_payload=prepared_response["client_payload"],
        )
        mark_task_progress(task_id, "翻译结果已生成")
        return {"conversation_id": payload.conversation_id, **{k: v for k, v in prepared_response.items() if k != "client_payload"}}


register_task_definition("continue_translation", ContinueTranslationTaskPayload, handle_continue_translation)


@router.post("/translations/{conversation_id}/continue")
async def continue_translation_route(
    conversation_id: str,
    action: str = Form(default="continue"),
    target_scope: str = Form(default="body"),
    poe_model: str = Form(default=settings.poe_model),
    api_key: str = Depends(get_api_key),
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    return await queue_continue_translation(
        conversation_id=conversation_id,
        action=action,
        target_scope=target_scope,
        poe_model=poe_model,
        api_key=api_key,
        session=session,
    )
