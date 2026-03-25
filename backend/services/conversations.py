from __future__ import annotations

import fastapi_poe as fp
from fastapi import HTTPException
from sqlmodel import Session

from ..integrations.poe import get_bot_response
from ..persistence import crud
from .message_utils import infer_message_metadata, parse_raw_translation_status_block


async def continue_conversation(
    conversation_id: str,
    new_user_message: str,
    poe_model: str,
    api_key: str,
    session: Session,
    save_to_record: bool,
    is_continue_command: bool = False,
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
        progress_callback("读取历史消息")
    db_messages = crud.get_messages(session, conversation_id)
    pdf_attachment = fp.Attachment(url=file_record.poe_url, content_type=file_record.content_type, name=file_record.poe_name)

    if progress_callback:
        progress_callback("构建 Poe 请求")
    poe_messages = [
        fp.ProtocolMessage(role="user", content=message.content, attachments=[pdf_attachment])
        if index == 0 and infer_message_metadata(message)["role"] == "user"
        else fp.ProtocolMessage(role=infer_message_metadata(message)["role"], content=message.content)
        for index, message in enumerate(db_messages)
    ]
    poe_messages.append(fp.ProtocolMessage(role="user", content=new_user_message))

    if progress_callback:
        progress_callback("等待 Poe 返回翻译结果")
    response_text = await get_bot_response(poe_messages, poe_model, api_key)
    response_status = parse_raw_translation_status_block(response_text)
    response_client_payload = {"translation_status": response_status} if response_status else None
    response_section_category = None

    if save_to_record:
        if progress_callback:
            progress_callback("写入会话消息到数据库")
        crud.create_messages(
            session,
            conversation_id,
            new_user_message,
            response_text,
            user_message_kind="continue_command" if is_continue_command else "user_message",
            user_visible_to_user=not is_continue_command,
            bot_section_category=response_section_category,
            bot_client_payload=response_client_payload,
        )

    if progress_callback:
        progress_callback("翻译结果已生成")
    return {
        "reply": response_text,
        "display_reply": response_text,
        "section_category": response_section_category,
        "translation_status": response_status,
    }
