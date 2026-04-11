from __future__ import annotations

from typing import Any
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlmodel import create_engine


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
            "You are an academic paper translation-plan extractor.\n\n"
            "Input: a paper PDF attachment.\n\n"
            "Task: return a compact JSON translation plan plus a concise terminology glossary.\n\n"
            "Rules:\n"
            "- Put abstract and main-body translation units in `units`.\n"
            "- Put appendices in `appendix_units`.\n"
            "- Do not mix appendices into `units`.\n"
            "- If an abstract exists, it must be the first item in `units`.\n"
            "- After that, list main-body sections in order.\n"
            "- Default: one top-level body section = one unit.\n"
            "- Prefer fewer units.\n"
            "- Prefer coarser units over finer ones whenever both are plausible.\n"
            "- Do not split just because subsections exist.\n"
            "- Split a top-level body section only if it is clearly too long for one translation step.\n"
            "- If splitting is necessary, split only by existing subsection boundaries.\n"
            "- Split format: `<top-level title> :: <subsection title>`.\n"
            "- If a section is split, do not also include the parent section as a separate unit.\n"
            "- Never output both a parent heading and any of its descendant headings in the same plan.\n"
            "- If parent and child headings coexist in the PDF outline, choose only one level for that branch.\n"
            "- For appendices or supplementary material, default to one first meaningful appendix-level section per unit, such as `Appendix A`, `A. ...`, `B. ...`, `C. ...`.\n"
            "- Do not keep a generic wrapper heading like `Supplementary Material` together with its child appendix sections.\n"
            "- Prefer `A. ...`, `B. ...`, `C. ...` over `A.1 ...`, `A.2 ...` unless the parent appendix section is clearly too long.\n"
            "- If an appendix section is split into subsection units, include only those subsection units and never the parent appendix section.\n"
            "- Keep output ordered, non-overlapping, and without duplicates.\n"
            "- Preserve visible heading text; normalize whitespace only.\n"
            "- Exclude title, authors, affiliations, emails, keywords, acknowledgements, references, bibliography, supplementary material, and standalone figure/table captions.\n"
            "- If appendices are clearly identifiable, put them in `appendix_units`; otherwise return an empty array.\n"
            "- Return `unsupported` if structure or boundaries are not reliable.\n"
            "- If `status` is `unsupported`, `units` and `appendix_units` must both be empty arrays.\n"
            "- If `status` is `ok`, `reason` must be an empty string.\n"
            "- Also extract a concise glossary in `glossary` with the most consistency-sensitive technical terms only.\n"
            "- Keep the glossary short and high-signal; prefer about 8 to 30 entries.\n"
            "- Each glossary item must contain `term` and `candidates`.\n"
            "- `candidates` must contain 1 to 3 concise Simplified Chinese translation candidates in preferred order.\n"
            "- Do not include explanations, notes, or low-value generic words.\n"
            "- If `status` is `unsupported`, `glossary` must be an empty array.\n\n"
            "Return JSON only with exactly this schema:\n\n"
            "{\n"
            "  \"status\": \"ok\" | \"unsupported\",\n"
            "  \"units\": [\"string\", \"...\"],\n"
            "  \"appendix_units\": [\"string\", \"...\"],\n"
            "  \"reason\": \"string\",\n"
            "  \"glossary\": [\n"
            "    {\n"
            "      \"term\": \"string\",\n"
            "      \"candidates\": [\"string\", \"...\"]\n"
            "    }\n"
            "  ]\n"
            "}"
        ),
        validation_alias="INITIAL_PROMPT",
    )
    continue_prompt: str = Field(
        default=(
            "You are an academic paper translator.\n\n"
            "You will receive:\n"
            "- a paper PDF attachment\n"
            "- an active ordered unit list\n"
            "- the current unit to translate\n\n"
            "Translate exactly the current unit from the PDF into Simplified Chinese.\n\n"
            "Rules:\n"
            "- Translate only the current unit.\n"
            "- Do not translate earlier or later units.\n"
            "- If the current unit is `Abstract` or `ABSTRACT`, translate the abstract only.\n"
            "- If the current unit contains no `::`, treat it as one whole section and translate that section only.\n"
            "- If the current unit contains `::`, translate only the subsection after `::`.\n"
            "- If the current unit contains `::` and that subsection is the first subsection under its top-level section, also translate the top-level section heading and the lead-in text between that heading and the subsection heading.\n"
            "- Do not output synthetic planner labels containing `::`; output only real visible headings from the PDF.\n"
            "- Abstract heading must be exactly `# 摘要`.\n"
            "- Top-level section headings must use `#` and translate the heading text into Simplified Chinese.\n"
            "- Second-level section headings must use `##` and translate the heading text into Simplified Chinese.\n"
            "- For non-abstract headings, preserve the original visible numbering or prefix exactly as shown in the PDF, and translate only the heading text after that prefix.\n"
            "- Examples of prefixes to preserve exactly include `1`, `1.`, `1.2`, `III.`, `A.`, and `Appendix A` when they are part of the visible heading.\n"
            "- Keep citations, formulas, symbols, numbering, and figure/table references unchanged whenever possible.\n"
            "- Exclude standalone figure captions and standalone table captions unless they are clearly part of the current unit’s running text.\n"
            "- If `CONFIRMED_GLOSSARY_JSON` is non-empty, prefer those confirmed term translations consistently whenever the source term appears.\n"
            "- Do not force glossary translations into sentences where the source term does not appear.\n"
            "- Do not add explanation, summary, or commentary.\n"
            "- Use Markdown.\n"
            "- If the current unit cannot be located reliably, or its boundaries are unclear in the PDF, return `UNSUPPORTED`.\n\n"
            "Output exactly:\n\n"
            "[TRANSLATION_STATUS_JSON]\n"
            "{\n"
            "  \"current_unit_id\": \"string\",\n"
            "  \"state\": \"OK\" | \"UNSUPPORTED\",\n"
            "  \"reason\": \"string\"\n"
            "}\n"
            "[/TRANSLATION_STATUS_JSON]\n\n"
            "If `state` is `OK`, output the translated text for the current unit after the status block.\n"
            "If `state` is `UNSUPPORTED`, output only the status block.\n\n"
            "Translate the current unit from the attached PDF.\n\n"
            "CONFIRMED_GLOSSARY_JSON:\n"
            "<<CONFIRMED_GLOSSARY_JSON>>\n\n"
            "ACTIVE_UNITS:\n"
            "<<ACTIVE_UNITS_JSON>>\n\n"
            "CURRENT_UNIT_ID:\n"
            "<<CURRENT_UNIT_ID>>"
        ),
        validation_alias="CONTINUE_PROMPT",
    )
    read_only: bool = Field(default=False, validation_alias="READ_ONLY")
    semantic_scholar_api_key: Optional[str] = Field(default=None, validation_alias="SEMANTIC_SCHOLAR_API_KEY")
    s2_api_key: Optional[str] = Field(default=None, validation_alias="S2_API_KEY")
    async_job_workers: int = Field(default=2, validation_alias="ASYNC_JOB_WORKERS")
    agent_ingest_token: Optional[str] = Field(default=None, validation_alias="AGENT_INGEST_TOKEN")
    db_pool_pre_ping: bool = Field(default=True, validation_alias="DB_POOL_PRE_PING")
    db_pool_recycle_seconds: int = Field(default=1800, validation_alias="DB_POOL_RECYCLE_SECONDS")
    db_pool_size: int = Field(default=5, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=10, validation_alias="DB_MAX_OVERFLOW")
    db_connect_timeout_seconds: int = Field(default=10, validation_alias="DB_CONNECT_TIMEOUT_SECONDS")
    db_tcp_keepalives: bool = Field(default=True, validation_alias="DB_TCP_KEEPALIVES")
    db_keepalives_idle_seconds: int = Field(default=30, validation_alias="DB_KEEPALIVES_IDLE_SECONDS")
    db_keepalives_interval_seconds: int = Field(default=10, validation_alias="DB_KEEPALIVES_INTERVAL_SECONDS")
    db_keepalives_count: int = Field(default=5, validation_alias="DB_KEEPALIVES_COUNT")


def _is_postgres_database_url(database_url: str) -> bool:
    normalized = (database_url or "").strip().lower()
    return normalized.startswith(("postgresql://", "postgresql+psycopg2://", "postgres://"))


def build_engine_kwargs(app_settings: Settings) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"echo": False}
    if not _is_postgres_database_url(app_settings.database_url):
        return kwargs

    connect_args: dict[str, Any] = {
        "connect_timeout": max(1, int(app_settings.db_connect_timeout_seconds)),
    }
    if app_settings.db_tcp_keepalives:
        connect_args.update(
            {
                "keepalives": 1,
                "keepalives_idle": max(1, int(app_settings.db_keepalives_idle_seconds)),
                "keepalives_interval": max(1, int(app_settings.db_keepalives_interval_seconds)),
                "keepalives_count": max(1, int(app_settings.db_keepalives_count)),
            }
        )

    kwargs.update(
        {
            "pool_pre_ping": bool(app_settings.db_pool_pre_ping),
            "pool_recycle": max(30, int(app_settings.db_pool_recycle_seconds)),
            "pool_size": max(1, int(app_settings.db_pool_size)),
            "max_overflow": max(0, int(app_settings.db_max_overflow)),
            "pool_use_lifo": True,
            "connect_args": connect_args,
        }
    )
    return kwargs


settings = Settings()
engine = create_engine(settings.database_url, **build_engine_kwargs(settings))
