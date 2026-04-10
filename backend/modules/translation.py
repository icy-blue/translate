from __future__ import annotations

import fastapi_poe as fp

from fastapi import APIRouter, Depends, Form, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from ..app.dependencies import check_read_only, get_api_key, get_db_session
from ..domain.message_payloads import (
    build_translation_status_payload,
    build_unit_translation_prompt,
    infer_message_metadata,
    normalize_raw_translation_result_payload,
    normalize_translation_glossary_payload,
    normalize_translation_plan_payload,
    normalize_translation_status_payload,
    parse_raw_translation_status_block,
    preprocess_bot_reply_for_storage,
    safe_json_loads,
)
from ..platform.config import engine, settings
from ..platform.gateways.poe import get_bot_response
from ..platform.task_runtime import enqueue_task, get_active_task, get_session_enqueue_lock, mark_task_progress, register_task_definition
from .conversations import add_message, create_message_pair, get_conversation, get_file_record, get_messages

router = APIRouter(tags=["translation"])


class ContinueTranslationTaskPayload(BaseModel):
    conversation_id: str
    action: str = "continue"
    target_scope: str = "body"
    poe_model: str
    api_key: str


class TranslationGlossaryEntryPayload(BaseModel):
    term: str
    candidates: list[str] = []
    selected: str = ""


class ConfirmTranslationGlossaryPayload(BaseModel):
    entries: list[TranslationGlossaryEntryPayload] = []


def _prepare_bot_response(
    response_text: str,
    *,
    translation_plan: dict[str, object],
    translation_status: dict[str, object],
    translation_glossary: dict[str, object] | None,
) -> dict:
    prepared_response = preprocess_bot_reply_for_storage(
        response_text,
        {
            "translation_plan": translation_plan,
            "translation_status": translation_status,
            "translation_glossary": translation_glossary,
        },
    )
    response_content = str(prepared_response["content"])
    return {
        "reply": response_text,
        "content": response_content,
        "display_reply": response_content,
        "section_category": None,
        "translation_plan": prepared_response["translation_plan"],
        "translation_status": prepared_response["translation_status"],
        "translation_glossary": prepared_response["translation_glossary"],
        "client_payload": prepared_response["client_payload"],
    }


def _get_latest_translation_context(session: Session, conversation_id: str) -> dict[str, object]:
    messages = get_messages(session, conversation_id)
    latest_translation_plan = None
    latest_translation_status = None
    latest_translation_glossary = None
    for message in reversed(messages):
        if infer_message_metadata(message)["role"] != "bot":
            continue
        payload = safe_json_loads(message.client_payload_json, {})
        if not isinstance(payload, dict):
            continue
        if latest_translation_plan is None:
            latest_translation_plan = normalize_translation_plan_payload(payload.get("translation_plan"))
        if latest_translation_status is None:
            latest_translation_status = normalize_translation_status_payload(payload.get("translation_status"))
        if latest_translation_glossary is None:
            latest_translation_glossary = normalize_translation_glossary_payload(payload.get("translation_glossary"))
        if latest_translation_plan is not None and latest_translation_status is not None and latest_translation_glossary is not None:
            break
    if latest_translation_plan is not None and latest_translation_status is not None:
        return {
            "translation_plan": latest_translation_plan,
            "translation_status": latest_translation_status,
            "translation_glossary": latest_translation_glossary,
        }
    raise HTTPException(
        status_code=409,
        detail="会话缺少可用的 translation_plan / translation_status，无法继续按 unit 协议续翻。",
    )


def _default_confirmed_glossary() -> dict[str, object]:
    return normalize_translation_glossary_payload({"status": "confirmed", "entries": []}) or {
        "protocol": "glossary_v1",
        "status": "confirmed",
        "entries": [],
    }


def _ensure_confirmed_glossary_for_translation(translation_glossary: dict[str, object] | None) -> dict[str, object]:
    normalized_glossary = normalize_translation_glossary_payload(translation_glossary) or _default_confirmed_glossary()
    if normalized_glossary["status"] != "confirmed" and normalized_glossary["entries"]:
        raise HTTPException(status_code=409, detail="术语词表尚未确认，请先确认术语后再继续翻译。")
    return normalized_glossary


def _glossary_signature_entries(translation_glossary: dict[str, object] | None) -> list[dict[str, object]]:
    normalized_glossary = normalize_translation_glossary_payload(translation_glossary) or _default_confirmed_glossary()
    return [
        {"term": entry["term"], "candidates": list(entry["candidates"])}
        for entry in normalized_glossary["entries"]
    ]


def _get_next_unit_id(translation_plan: dict[str, object], translation_status: dict[str, object], target_scope: str) -> str:
    normalized_plan = normalize_translation_plan_payload(translation_plan)
    normalized_status = normalize_translation_status_payload(translation_status)
    if normalized_plan is None or normalized_status is None:
        return ""
    completed_ids = set(normalized_status["completed_unit_ids"])
    if target_scope == "appendix":
        if any(unit_id not in completed_ids for unit_id in normalized_plan["units"]):
            return ""
        units = normalized_plan["appendix_units"]
    else:
        units = normalized_plan["units"]
    for unit_id in units:
        if unit_id not in completed_ids:
            return unit_id
    return ""


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
    if payload.target_scope not in {"body", "appendix"}:
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
        latest_status = normalize_translation_status_payload(latest_context["translation_status"])
        latest_plan = normalize_translation_plan_payload(latest_context["translation_plan"])
        latest_glossary = _ensure_confirmed_glossary_for_translation(latest_context.get("translation_glossary"))
        if latest_status is None or latest_plan is None:
            raise HTTPException(status_code=409, detail="最新翻译状态不可用。")
        if latest_status["state"] in {"UNSUPPORTED", "ALL_DONE"}:
            raise HTTPException(status_code=409, detail="当前会话没有可继续的 unit。")
        next_unit_id = _get_next_unit_id(latest_plan, latest_status, payload.target_scope)
        if not next_unit_id:
            raise HTTPException(status_code=409, detail="当前 scope 没有可继续的 unit。")
        active_units = latest_plan["units"] if payload.target_scope == "body" else latest_plan["appendix_units"]
        prompt = build_unit_translation_prompt(
            settings.continue_prompt,
            active_units=active_units,
            current_unit_id=next_unit_id,
            translation_glossary=latest_glossary,
        )
        pdf_attachment = fp.Attachment(url=file_record.poe_url, content_type=file_record.content_type, name=file_record.poe_name)
        mark_task_progress(task_id, "等待 Poe 返回翻译结果")
        response_text = await get_bot_response(
            [fp.ProtocolMessage(role="user", content=prompt, attachments=[pdf_attachment])],
            payload.poe_model,
            payload.api_key,
        )
        raw_translation_result = normalize_raw_translation_result_payload(parse_raw_translation_status_block(response_text))
        if raw_translation_result is None:
            raw_translation_result = {
                "current_unit_id": next_unit_id,
                "state": "UNSUPPORTED",
                "reason": "translator_status_missing",
            }
        completed_unit_ids = list(latest_status["completed_unit_ids"])
        if raw_translation_result and raw_translation_result["state"] == "OK" and next_unit_id not in completed_unit_ids:
            completed_unit_ids.append(next_unit_id)
        canonical_status = build_translation_status_payload(
            latest_plan,
            completed_unit_ids=completed_unit_ids,
            current_unit_id=next_unit_id,
            attempted_scope=payload.target_scope,
            raw_translation_result=raw_translation_result,
        )
        prepared_response = _prepare_bot_response(
            response_text,
            translation_plan=latest_plan,
            translation_status=canonical_status,
            translation_glossary=latest_glossary,
        )
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


@router.put("/translations/{conversation_id}/glossary")
async def confirm_translation_glossary_route(
    conversation_id: str,
    payload: ConfirmTranslationGlossaryPayload,
    session: Session = Depends(get_db_session),
    _read_only: None = Depends(check_read_only),
):
    conversation = get_conversation(session, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    latest_context = _get_latest_translation_context(session, conversation_id)
    latest_status = normalize_translation_status_payload(latest_context["translation_status"])
    latest_plan = normalize_translation_plan_payload(latest_context["translation_plan"])
    latest_glossary = normalize_translation_glossary_payload(latest_context.get("translation_glossary")) or _default_confirmed_glossary()
    if latest_status is None or latest_plan is None:
        raise HTTPException(status_code=409, detail="最新翻译状态不可用。")
    if latest_status["completed_unit_count"] > 0:
        raise HTTPException(status_code=409, detail="已有翻译内容生成，当前版本不支持中途修改术语词表。")

    submitted_glossary = normalize_translation_glossary_payload(
        {
            "status": "confirmed",
            "entries": [entry.model_dump() for entry in payload.entries],
        }
    )
    if submitted_glossary is None:
        raise HTTPException(status_code=400, detail="提交的术语词表无效。")

    if _glossary_signature_entries(latest_glossary) != _glossary_signature_entries(submitted_glossary):
        raise HTTPException(status_code=409, detail="术语词表已变化，请刷新页面后重新确认。")

    add_message(
        session,
        conversation_id=conversation_id,
        content="Confirm translation glossary selections.",
        message_kind="user_message",
        visible_to_user=False,
    )
    add_message(
        session,
        conversation_id=conversation_id,
        content="",
        message_kind="bot_reply",
        visible_to_user=False,
        client_payload={
            "translation_plan": latest_plan,
            "translation_status": latest_status,
            "translation_glossary": submitted_glossary,
        },
    )
    session.commit()
    return {
        "conversation_id": conversation_id,
        "translation_plan": latest_plan,
        "translation_status": latest_status,
        "translation_glossary": submitted_glossary,
    }
