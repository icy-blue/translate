from typing import Optional
import fastapi_poe as fp
from config import settings
from paper_tags import (
    build_category_selection_prompt,
    build_tag_payloads,
    build_tagging_followup_prompt,
    parse_category_codes,
    parse_tag_codes,
)

async def extract_title_from_pdf(pdf_attachment: fp.Attachment, api_key: str, model: str) -> Optional[str]:
    """
    Extracts the title from a PDF using a Poe bot.
    """
    prompt = settings.title_prompt
    message = fp.ProtocolMessage(role="user", content=prompt, attachments=[pdf_attachment])
    title_text = ""
    async for part in fp.get_bot_response(
        messages=[message],
        bot_name=model,
        api_key=api_key
    ):
        title_text += part.text
    return title_text.strip() or None

async def get_bot_response(messages: list[fp.ProtocolMessage], bot_name: str, api_key: str) -> str:
    """
    Gets a response from a Poe bot.
    """
    response_text = ""
    async for partial in fp.get_bot_response(
        messages=messages,
        bot_name=bot_name,
        api_key=api_key
    ):
        response_text += partial.text
    return response_text

async def upload_file(file, api_key: str, file_name: str) -> fp.Attachment:
    """
    Uploads a file to Poe.
    """
    return await fp.upload_file(file, api_key=api_key, file_name=file_name)


async def classify_paper_tags(title: str, abstract: str, bot_name: str, api_key: str) -> list[dict]:
    """
    Classifies a paper into the maintained tag library with a two-stage compact prompt flow.
    """
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
