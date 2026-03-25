from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # ✅ v2 使用 model_config
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # database connection string
    database_url: str = Field(
        default="sqlite:///translations.db",
        validation_alias="DATABASE_URL",
    )

    poe_model: str = Field(
        default="GPT-5.2-Instant",
        validation_alias="POE_MODEL",
    )

    title_prompt: str = Field(
        default="请查看附加的 PDF 文档，提取论文标题。标题可能由多行组成，仅返回标题文本，不要翻译或添加其他注释。",
        validation_alias="TITLE_PROMPT",
    )

    initial_prompt: str = Field(
        default=(
            "请翻译这篇论文。目标是让用户无需手动判断是否已经翻译完毕。\n\n"
            "默认翻译范围：\n"
            "1. 包含：摘要、正文各一级章节。\n"
            "2. 不包含：致谢、参考文献、附录，除非用户明确要求继续翻译这些部分。\n"
            "3. 因此，“已完成”默认指“默认翻译范围已完成”。\n\n"
            "翻译策略：\n"
            "1. 先通读并识别全文结构，再开始翻译。\n"
            "2. 采用“自适应分块”而不是固定“一章一轮”：\n"
            "   - 尽量在一级章节或二级小节边界结束；\n"
            "   - 相邻短章节可合并为一轮；\n"
            "   - 超长章节可拆为多轮，但不要在一个小节中途截断；\n"
            "   - 不要重复，不要跳过。\n"
            "3. 第一轮先输出“全文结构概览（只列目录）”，然后翻译摘要。\n"
            "4. 之后当用户发送“继续”时，翻译下一块尚未翻译的内容。\n\n"
            "每一轮都必须先输出下面这个状态块，字段名保持不变：\n\n"
            "[TRANSLATION_STATUS]\n"
            "scope=body_only\n"
            "completed=...\n"
            "current=...\n"
            "next=...\n"
            "remaining=...\n"
            "state=IN_PROGRESS|BODY_DONE|ALL_DONE\n"
            "phase=body|appendix|acknowledgements|references|done\n"
            "available_scope_extensions=appendix,acknowledgements,references\n"
            "next_action_type=continue|custom_message|stop\n"
            "next_action_command=...\n"
            "next_action_target_scope=body|appendix|acknowledgements|references|none\n"
            "recommended_stop_reason=body_done|all_done|unsupported\n"
            "[/TRANSLATION_STATUS]\n\n"
            "输出规则：\n"
            "1. 正文一级标题使用 #，二级标题使用 ##。\n"
            "2. 忠实翻译，不省略公式、图表编号、引用编号。\n"
            "3. 如果 state=IN_PROGRESS，明确写出“回复‘继续’即可翻译下一块”。\n"
            "4. 如果正文已完成，输出 state=BODY_DONE，并明确说明：\n"
            "   - 正文已完成；\n"
            "   - 附录/致谢/参考文献分别是否未翻译；\n"
            "   - available_scope_extensions 中写出仍可继续的范围；\n"
            "   - next_action_* 字段中写出如需继续时的建议命令。\n"
            "5. 如果全部完成或用户在完成后再次发送“继续”，不要再生成新的正文翻译，只返回完成提示与可选后续操作。"
        ),
        validation_alias="INITIAL_PROMPT",
    )

    read_only: bool = Field(
        default=False,
        validation_alias="READ_ONLY",
    )

    semantic_scholar_api_key: Optional[str] = Field(
        default=None,
        validation_alias="SEMANTIC_SCHOLAR_API_KEY",
    )

    s2_api_key: Optional[str] = Field(
        default=None,
        validation_alias="S2_API_KEY",
    )

    async_job_workers: int = Field(
        default=2,
        validation_alias="ASYNC_JOB_WORKERS",
    )

    agent_ingest_token: Optional[str] = Field(
        default=None,
        validation_alias="AGENT_INGEST_TOKEN",
    )


settings = Settings()
