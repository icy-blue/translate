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
        default="翻译这篇论文，每次翻译一章（摘要单独算一章）。摘要、章节用 1 级标题，子章节为 2 级标题。当我说“继续”时翻译下一章，直到结束。请先翻译摘要。",
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
