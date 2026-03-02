from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    # database connection string
    database_url: str = Field("sqlite:///translations.db", env="DATABASE_URL")

    # Poe model and prompts
    poe_model: str = Field("GPT-5.2-Instant", env="POE_MODEL")
    title_prompt: str = Field(
        "请查看附加的 PDF 文档，提取论文标题。标题可能由多行组成，仅返回标题文本，不要翻译或添加其他注释。",
        env="TITLE_PROMPT"
    )
    initial_prompt: str = Field(
        "翻译这篇论文，每次翻译一章（摘要单独算一章）。摘要、章节用 1 级标题，子章节为 2 级标题。当我说“继续”时翻译下一章，直到结束。请先翻译摘要。",
        env="INITIAL_PROMPT"
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
