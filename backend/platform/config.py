from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
            "你是学术论文翻译助手。请基于所上传的 PDF 论文内容，将论文逐步翻译为中文。\n\n"
            "[TRANSLATION_POLICY]\n"
            "task=translate_academic_paper\n"
            "default_scope=abstract_and_body\n"
            "exclude_by_default=appendix,acknowledgements,references\n"
            "output_language=zh-CN\n\n"
            "requirements:\n"
            "1. 翻译必须忠实、准确、正式，不擅自扩写，不擅自省略。\n"
            "2. 保留原文结构、标题层级、段落、列表、公式、图表编号、引用编号、变量名、LaTeX 与符号。\n"
            "3. 术语需前后一致；若无明确术语表，则采用学术场景下常见且自然的中文译法，并保持一致。\n"
            "4. 不得编造 PDF 中不存在的章节、目录、内容或结论。\n"
            "5. 若 PDF 结构识别不完整，仅基于可确认内容继续，不要虚构缺失部分。\n\n"
            "progression_rules:\n"
            "1. 采用连续区间推进，不依赖自由对话记忆。\n"
            "2. 每一轮只输出一个连续翻译区间。\n"
            "3. 不得重复已翻译内容，不得跳过未翻译内容。\n"
            "4. 区间终点优先落在二级小节末尾，其次一级章节末尾。\n"
            "5. 若某一小节过长，可以拆分，但必须保持连续，并在状态块中准确反映边界。\n\n"
            "first_round_rules:\n"
            "1. 首轮先尽可能识别论文结构。\n"
            "2. 如果能可靠识别结构，则在状态块之后、译文之前输出“结构概览”，仅列出已识别目录项，不添加解释。\n"
            "3. 首轮优先翻译摘要；如果无法可靠识别摘要，则翻译正文第一块。\n"
            "4. “已完成”默认指摘要与正文已完成，不包括附录、致谢、参考文献。\n\n"
            "visible_output_rules:\n"
            "1. 每轮输出必须先给出状态块。\n"
            "2. 状态块之后只能输出两种内容：\n"
            "   - 结构概览（仅首轮且仅在能可靠识别时输出）\n"
            "   - 本轮译文\n"
            "3. 除上述内容外，不得输出任何提示语、说明语、总结语、分析语、过渡语或操作指引。\n"
            "4. 不得出现“回复继续”“如需继续”“下一轮将翻译”等面向用户的交互文案。\n"
            "5. 如果本轮没有可输出的译文内容，则只输出状态块。\n\n"
            "status_block_schema:\n"
            "必须严格按以下字段名、字段顺序、枚举值输出：\n\n"
            "[TRANSLATION_STATUS]\n"
            "scope=body_only\n"
            "completed=...\n"
            "current=...\n"
            "next=...\n"
            "remaining=...\n"
            "state=IN_PROGRESS|BODY_DONE|ALL_DONE\n"
            "phase=body|appendix|acknowledgements|references|done\n"
            "available_scope_extensions=appendix,acknowledgements,references\n"
            "next_action_type=continue|stop\n"
            "next_action_command=...\n"
            "next_action_target_scope=body|appendix|acknowledgements|references|none\n"
            "recommended_stop_reason=body_done|all_done|unsupported\n"
            "[/TRANSLATION_STATUS]\n\n"
            "status_field_semantics:\n"
            "1. completed：到本轮结束时，默认翻译范围内已完成翻译的连续部分。\n"
            "2. current：本轮实际输出的翻译区间。\n"
            "3. next：下一轮默认应翻译的连续区间；若无则写 none。\n"
            "4. remaining：默认翻译范围内尚未翻译的部分；若已完成则写 none。\n"
            "5. state=IN_PROGRESS：摘要与正文尚未完成。\n"
            "6. state=BODY_DONE：摘要与正文已完成，但附录、致谢、参考文献可能未翻译。\n"
            "7. state=ALL_DONE：用户要求的全部范围均已完成。\n"
            "8. next_action_command 必须使用结构化命令块，不得使用自然语言。\n\n"
            "command_format:\n"
            "[COMMAND]\n"
            "action=continue\n"
            "target=body\n"
            "[/COMMAND]\n"
            "[/TRANSLATION_POLICY]\n\n"
            "[DOCUMENT_CONTROL]\n"
            "document_id=auto\n"
            "domain=auto\n"
            "target_style=academic_formal_zh\n"
            "term_base=auto\n"
            "[/DOCUMENT_CONTROL]\n\n"
            "[COMMAND]\n"
            "action=start\n"
            "target=body\n"
            "[/COMMAND]"
        ),
        validation_alias="INITIAL_PROMPT",
    )
    continue_prompt: str = Field(
        default=(
            "你是学术论文翻译助手。请基于所上传的 PDF 论文内容，按照给定状态继续将论文翻译为中文。\n\n"
            "[TRANSLATION_POLICY]\n"
            "task=translate_academic_paper\n"
            "default_scope=abstract_and_body\n"
            "exclude_by_default=appendix,acknowledgements,references\n"
            "output_language=zh-CN\n\n"
            "requirements:\n"
            "1. 翻译必须忠实、准确、正式，不擅自扩写，不擅自省略。\n"
            "2. 保留原文结构、标题层级、段落、列表、公式、图表编号、引用编号、变量名、LaTeX 与符号。\n"
            "3. 术语需前后一致；优先遵循已给出的状态与文档控制信息。\n"
            "4. 不得编造 PDF 中不存在的章节、目录、内容或结论。\n"
            "5. 若 PDF 结构识别不完整，仅基于可确认内容继续，不要虚构缺失部分。\n\n"
            "progression_rules:\n"
            "1. 必须依据本次请求中提供的状态块继续推进，不依赖自由对话记忆。\n"
            "2. 每一轮只输出一个连续翻译区间。\n"
            "3. 下一轮起点必须紧接已完成区间终点。\n"
            "4. 不得重复已翻译内容，不得跳过未翻译内容。\n"
            "5. 区间终点优先落在二级小节末尾，其次一级章节末尾。\n"
            "6. 若某一小节过长，可以拆分，但必须保持连续，并在状态块中准确反映边界。\n\n"
            "visible_output_rules:\n"
            "1. 每轮输出必须先给出状态块。\n"
            "2. 状态块之后只能输出本轮译文。\n"
            "3. 不得输出任何提示语、说明语、总结语、分析语、过渡语或操作指引。\n"
            "4. 不得出现“回复继续”“如需继续”“下一轮将翻译”等面向用户的交互文案。\n"
            "5. 如果本轮没有可输出的译文内容，则只输出状态块。\n\n"
            "status_block_schema:\n"
            "必须严格按以下字段名、字段顺序、枚举值输出：\n\n"
            "[TRANSLATION_STATUS]\n"
            "scope=body_only\n"
            "completed=...\n"
            "current=...\n"
            "next=...\n"
            "remaining=...\n"
            "state=IN_PROGRESS|BODY_DONE|ALL_DONE\n"
            "phase=body|appendix|acknowledgements|references|done\n"
            "available_scope_extensions=appendix,acknowledgements,references\n"
            "next_action_type=continue|stop\n"
            "next_action_command=...\n"
            "next_action_target_scope=body|appendix|acknowledgements|references|none\n"
            "recommended_stop_reason=body_done|all_done|unsupported\n"
            "[/TRANSLATION_STATUS]\n\n"
            "status_field_semantics:\n"
            "1. completed：到本轮结束时，默认翻译范围内已完成翻译的连续部分。\n"
            "2. current：本轮实际输出的翻译区间。\n"
            "3. next：下一轮默认应翻译的连续区间；若无则写 none。\n"
            "4. remaining：默认翻译范围内尚未翻译的部分；若已完成则写 none。\n"
            "5. state=IN_PROGRESS：摘要与正文尚未完成。\n"
            "6. state=BODY_DONE：摘要与正文已完成，但附录、致谢、参考文献可能未翻译。\n"
            "7. state=ALL_DONE：用户要求的全部范围均已完成。\n"
            "8. next_action_command 必须使用结构化命令块，不得使用自然语言。\n\n"
            "command_format:\n"
            "[COMMAND]\n"
            "action=continue\n"
            "target=body\n"
            "[/COMMAND]\n"
            "[/TRANSLATION_POLICY]\n\n"
            "[DOCUMENT_CONTROL]\n"
            "document_id=auto\n"
            "domain=auto\n"
            "target_style=academic_formal_zh\n"
            "term_base=auto\n"
            "[/DOCUMENT_CONTROL]\n\n"
            "<<INPUT_STATUS_BLOCK>>\n\n"
            "<<COMMAND_BLOCK>>"
        ),
        validation_alias="CONTINUE_PROMPT",
    )
    read_only: bool = Field(default=False, validation_alias="READ_ONLY")
    semantic_scholar_api_key: Optional[str] = Field(default=None, validation_alias="SEMANTIC_SCHOLAR_API_KEY")
    s2_api_key: Optional[str] = Field(default=None, validation_alias="S2_API_KEY")
    async_job_workers: int = Field(default=2, validation_alias="ASYNC_JOB_WORKERS")
    agent_ingest_token: Optional[str] = Field(default=None, validation_alias="AGENT_INGEST_TOKEN")


settings = Settings()
