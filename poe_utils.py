from typing import Optional
import fastapi_poe as fp
from config import settings

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
