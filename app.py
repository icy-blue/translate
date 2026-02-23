import fastapi_poe as fp
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import tempfile
import os
import uuid

app = FastAPI()

# ✅ 允许前端访问（开发阶段）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ 简单内存存储（生产请改数据库）
conversations = {}


@app.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    api_key: str = Form(...)
):
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key is required")

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        # ✅ 读取文件
        file_bytes = await file.read()

        if len(file_bytes) == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        # ✅ 写入临时文件
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(file_bytes)
            temp_path = tmp.name

        # ✅ 上传到 Poe
        with open(temp_path, "rb") as f:
            pdf_attachment = await fp.upload_file(
                f,
                api_key=api_key
            )

        # ✅ 删除临时文件
        os.remove(temp_path)

        # ✅ 构造初始消息
        message = fp.ProtocolMessage(
            role="user",
            content="""
翻译这篇论文，每次翻译一章（摘要单独算一章）。
摘要、章节用 1 级标题，子章节为 2 级标题，段首小标题无需设置标题。
当我说“继续”时翻译下一章。
直到翻译完全文。
请先翻译摘要。
""",
            attachments=[pdf_attachment]
        )

        response_text = ""

        # ✅ 调用 Claude（文件支持最好）
        async for partial in fp.get_bot_response(
            messages=[message],
            bot_name="GPT-5.2-Instant",
            api_key=api_key
        ):
            response_text += partial.text

        conversation_id = str(uuid.uuid4())

        conversations[conversation_id] = {
            "messages": [message],
            "api_key": api_key,
            "translation": response_text
        }

        return {
            "conversation_id": conversation_id,
            "reply": response_text
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/continue/{conversation_id}")
async def continue_translation(conversation_id: str):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")

    session = conversations[conversation_id]
    api_key = session["api_key"]

    continue_message = fp.ProtocolMessage(
        role="user",
        content="继续"
    )

    session["messages"].append(continue_message)

    response_text = ""

    try:
        async for partial in fp.get_bot_response(
            messages=session["messages"],
            bot_name="GPT-5.2-Instant",
            api_key=api_key
        ):
            response_text += partial.text

        assistant_message = fp.ProtocolMessage(
            role="bot",
            content=response_text
        )

        session["messages"].append(assistant_message)
        session["translation"] += "\n\n" + response_text

        return {"reply": response_text}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/result/{conversation_id}")
async def get_result(conversation_id: str):
    if conversation_id not in conversations:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "full_translation": conversations[conversation_id]["translation"]
    }


# ✅ 支持直接 python app.py 启动
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)