from __future__ import annotations

from typing import Iterable

BOT_MESSAGE_KIND = "bot_reply"
USER_MESSAGE_KINDS = ("system_prompt", "continue_command", "user_message")
MESSAGE_KINDS = (*USER_MESSAGE_KINDS, BOT_MESSAGE_KIND)
LEGACY_INITIAL_PROMPTS = (
    "翻译这篇论文，每次翻译一章（摘要单独算一章）。摘要、章节用 1 级标题，子章节为 2 级标题。当我说“继续”时翻译下一章，直到结束。请先翻译摘要。",
)


def _normalize_text(text: str | None) -> str:
    return "".join((text or "").split())


def normalize_message_kind(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized if normalized in MESSAGE_KINDS else None


def role_from_message_kind(message_kind: str | None) -> str:
    normalized = normalize_message_kind(message_kind)
    if normalized == BOT_MESSAGE_KIND:
        return "bot"
    return "user"


def is_bot_message_kind(message_kind: str | None) -> bool:
    return role_from_message_kind(message_kind) == "bot"


def infer_message_kind(
    *,
    message_kind: str | None = None,
    message_type: str | None = None,
    role: str | None = None,
    content: str | None = None,
    initial_prompts: Iterable[str] = (),
) -> str:
    normalized_kind = normalize_message_kind(message_kind)
    if normalized_kind:
        return normalized_kind
    normalized_type = normalize_message_kind(message_type)
    if normalized_type:
        return normalized_type
    normalized_role = (role or "").strip().lower()
    if normalized_role == "bot":
        return BOT_MESSAGE_KIND
    normalized_content = _normalize_text(content)
    if normalized_content == _normalize_text("继续"):
        return "continue_command"
    if normalized_content in {_normalize_text(prompt) for prompt in initial_prompts if prompt}:
        return "system_prompt"
    return "user_message"
