from __future__ import annotations

import re
from typing import Any, Optional


TRANSLATION_STATUS_PATTERN = re.compile(
    r"\[TRANSLATION_STATUS\]\s*(.*?)\s*\[/TRANSLATION_STATUS\]",
    re.DOTALL,
)
SEPARATOR_LINE_PATTERN = re.compile(r"^\s*[-*_—]{3,}\s*$")
SECTION_CATEGORIES = (
    "abstract",
    "body",
    "acknowledgements",
    "references",
    "appendix",
)
SECTION_HEADING_PATTERNS = {
    "abstract": (
        re.compile(r"^#{1,6}\s*(摘要|abstract)\s*$", re.IGNORECASE),
        re.compile(r"^(摘要|abstract)\s*[:：]?\s*$", re.IGNORECASE),
    ),
    "acknowledgements": (
        re.compile(r"^#{1,6}\s*(致谢|acknowledg(?:e)?ments?)\s*$", re.IGNORECASE),
        re.compile(r"^(致谢|acknowledg(?:e)?ments?)\s*[:：]?\s*$", re.IGNORECASE),
    ),
    "references": (
        re.compile(r"^#{1,6}\s*(参考文献|references?|bibliography)\s*$", re.IGNORECASE),
        re.compile(r"^(参考文献|references?|bibliography)\s*[:：]?\s*$", re.IGNORECASE),
    ),
    "appendix": (
        re.compile(r"^#{1,6}\s*(附录|appendix|supplement(?:ary|al)?(?:\s+material)?)\b", re.IGNORECASE),
        re.compile(r"^(附录|appendix|supplement(?:ary|al)?(?:\s+material)?)\b", re.IGNORECASE),
    ),
}
BODY_HEADING_PATTERNS = (
    re.compile(r"^#{1,6}\s+\S+"),
    re.compile(r"^(?:第\s*[0-9一二三四五六七八九十IVXLC]+\s*[章节]|[IVXLC0-9]+[\.、．)]\s*\S+)", re.IGNORECASE),
)
PHASE_TO_SECTION = {
    "body": "body",
    "appendix": "appendix",
    "acknowledgements": "acknowledgements",
    "references": "references",
}


def strip_translation_status_block(content: Optional[str]) -> str:
    return TRANSLATION_STATUS_PATTERN.sub("", content or "").strip()


def _is_separator_line(line: str) -> bool:
    return bool(SEPARATOR_LINE_PATTERN.match(line or ""))


def _iter_meaningful_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not _is_separator_line(line.strip())
    ]


def _match_heading_category(line: str) -> Optional[str]:
    stripped = (line or "").strip()
    for category, patterns in SECTION_HEADING_PATTERNS.items():
        if any(pattern.match(stripped) for pattern in patterns):
            return category
    return None


def _is_body_heading(line: str) -> bool:
    stripped = (line or "").strip()
    return any(pattern.match(stripped) for pattern in BODY_HEADING_PATTERNS)


def _classify_text_block(text: str) -> Optional[str]:
    meaningful_lines = _iter_meaningful_lines(text)
    if not meaningful_lines:
        return None

    first_category_index = None
    first_category = None
    first_body_heading_index = None
    for index, line in enumerate(meaningful_lines):
        category = _match_heading_category(line)
        if category is not None:
            first_category_index = index
            first_category = category
            break
        if first_body_heading_index is None and _is_body_heading(line):
            first_body_heading_index = index

    if first_category is not None:
        return first_category
    if first_body_heading_index is not None:
        return "body"
    return "body" if text.strip() else None


def classify_message_section(
    *,
    original_content: Optional[str],
    display_content: Optional[str] = None,
    translation_status: Optional[dict[str, Any]] = None,
) -> dict[str, Optional[str]]:
    original_text = strip_translation_status_block(original_content)
    visible_text = (display_content if display_content is not None else original_text).strip()

    if visible_text:
        category = _classify_text_block(visible_text)
        if category is not None:
            return {
                "section_category": category,
                "source": "display_content",
            }

    if isinstance(translation_status, dict):
        phase = str(translation_status.get("phase", "")).strip().lower()
        if phase in PHASE_TO_SECTION:
            return {
                "section_category": PHASE_TO_SECTION[phase],
                "source": "translation_status.phase",
            }

    if original_text:
        category = _classify_text_block(original_text)
        if category is not None:
            return {
                "section_category": category,
                "source": "original_content",
            }

    return {
        "section_category": None,
        "source": "empty",
    }
