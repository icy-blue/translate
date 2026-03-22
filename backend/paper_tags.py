from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CategoryDefinition:
    category_code: str
    category_label: str
    category_label_en: str


@dataclass(frozen=True)
class TagDefinition:
    category_code: str
    category_label: str
    category_label_en: str
    tag_code: str
    tag_label: str
    tag_label_en: str

    @property
    def path(self) -> str:
        return f"{self.category_label}/{self.tag_label}"

    @property
    def path_en(self) -> str:
        return f"{self.category_label_en}/{self.tag_label_en}"


TAG_TREE_PATH = Path(__file__).resolve().parents[1] / "data" / "tag_tree.json"
_RAW_TAG_TREE = json.loads(TAG_TREE_PATH.read_text(encoding="utf-8"))

CATEGORY_MAP: dict[str, CategoryDefinition] = {
    item["code"]: CategoryDefinition(
        category_code=item["code"],
        category_label=item["label"],
        category_label_en=item["label_en"],
    )
    for item in _RAW_TAG_TREE
}

TAG_TREE: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = tuple(
    (
        item["code"],
        item["label"],
        tuple((tag["code"], tag["label"]) for tag in item["tags"]),
    )
    for item in _RAW_TAG_TREE
)

TAG_MAP: dict[str, TagDefinition] = {}
for item in _RAW_TAG_TREE:
    category = CATEGORY_MAP[item["code"]]
    for tag in item["tags"]:
        TAG_MAP[tag["code"]] = TagDefinition(
            category_code=category.category_code,
            category_label=category.category_label,
            category_label_en=category.category_label_en,
            tag_code=tag["code"],
            tag_label=tag["label"],
            tag_label_en=tag["label_en"],
        )


def _format_prompt_category(item: dict) -> str:
    tag_items = " ".join(f"{tag['code']}{tag['label']}" for tag in item["tags"])
    return f"{item['code']}:{tag_items}"


TAG_PROMPT_LIBRARY = "\n".join(
    _format_prompt_category(item) for item in _RAW_TAG_TREE
)

CATEGORY_PROMPT_LIBRARY = ",".join(
    f"{item['code']}={item['label']}"
    for item in _RAW_TAG_TREE
)

ALLOWED_CATEGORY_CODES = tuple(item["code"] for item in _RAW_TAG_TREE)


def get_tag_definition(tag_code: str) -> TagDefinition | None:
    return TAG_MAP.get(tag_code)


def get_tag_library_payload(usage_counts: dict[str, int] | None = None) -> list[dict]:
    counts = usage_counts or {}
    payload: list[dict] = []
    for item in _RAW_TAG_TREE:
        tags = []
        for tag in item["tags"]:
            definition = TAG_MAP[tag["code"]]
            tags.append(
                {
                    "category_code": definition.category_code,
                    "category_label": definition.category_label,
                    "category_label_en": definition.category_label_en,
                    "tag_code": definition.tag_code,
                    "tag_label": definition.tag_label,
                    "tag_label_en": definition.tag_label_en,
                    "tag_path": definition.path,
                    "tag_path_en": definition.path_en,
                    "usage_count": counts.get(definition.tag_code, 0),
                }
            )
        payload.append(
            {
                "category_code": item["code"],
                "category_label": item["label"],
                "category_label_en": item["label_en"],
                "tags": tags,
            }
        )
    return payload


def extract_abstract_for_tagging(message_content: str, max_chars: int = 1200) -> str:
    if not message_content:
        return ""

    text = re.sub(r"```.*?```", " ", message_content, flags=re.DOTALL)
    text = text.replace("\r\n", "\n")
    lines = [line.strip() for line in text.splitlines()]

    kept_lines: list[str] = []
    for line in lines:
        if not line:
            continue
        if re.fullmatch(r"#{1,6}\s*(摘要|abstract)\s*", line, flags=re.IGNORECASE):
            continue
        if re.fullmatch(r"(摘要|abstract)\s*[:：]?\s*", line, flags=re.IGNORECASE):
            continue
        if re.match(r"^#{1,6}\s+", line) and kept_lines:
            break
        kept_lines.append(re.sub(r"^[*-]\s*", "", line))

    compact_text = " ".join(kept_lines) if kept_lines else " ".join(line for line in lines if line)
    compact_text = re.sub(r"\s+", " ", compact_text).strip()
    if len(compact_text) <= max_chars:
        return compact_text

    trimmed = compact_text[:max_chars].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed


def build_category_selection_prompt(title: str, abstract: str) -> str:
    safe_title = _compact_text(title, max_chars=240) or "-"
    safe_abstract = _compact_text(abstract, max_chars=1200) or "-"
    return (
        "你是论文标签分类器。先判断相关分类组。\n"
        "规则:只选最相关的2到4组;不确定就省略。\n"
        "输出:仅一行字母代码,逗号分隔,如M,T,S。\n"
        f"分类组:{CATEGORY_PROMPT_LIBRARY}\n"
        f"题:{safe_title}\n"
        f"摘:{safe_abstract}"
    )


def build_tagging_followup_prompt(category_codes: list[str]) -> str:
    selected_codes = [code for code in category_codes if code in ALLOWED_CATEGORY_CODES]
    selected_library = "\n".join(
        _format_prompt_category(item)
        for item in _RAW_TAG_TREE
        if item["code"] in selected_codes
    )
    return (
        "基于上文论文内容，仅在下列分类组中选标签。\n"
        "规则:只从库中选;每组最多2个;总数<=8;不确定就省略。\n"
        "输出:仅一行逗号分隔标签代码,如L3,M4,T7,S2。\n"
        f"标签库:\n{selected_library or TAG_PROMPT_LIBRARY}"
    )


def parse_category_codes(raw_response: str) -> list[str]:
    if not raw_response:
        return []

    allowed = set(ALLOWED_CATEGORY_CODES)
    seen: set[str] = set()
    ordered_codes: list[str] = []
    for match in re.finditer(r"\b([A-Z])\b", raw_response.upper()):
        code = match.group(1)
        if code not in allowed or code in seen:
            continue
        seen.add(code)
        ordered_codes.append(code)
    return ordered_codes


def parse_tag_codes(raw_response: str) -> list[str]:
    if not raw_response:
        return []

    seen: set[str] = set()
    ordered_codes: list[str] = []
    for match in re.finditer(r"\b([LMTSAP]\d{1,2})\b", raw_response.upper()):
        code = match.group(1)
        if code not in TAG_MAP or code in seen:
            continue
        seen.add(code)
        ordered_codes.append(code)
    return ordered_codes


def resolve_tag_codes(tag_codes: list[str]) -> list[TagDefinition]:
    return [TAG_MAP[code] for code in tag_codes if code in TAG_MAP]


def build_tag_payloads(tag_codes: list[str], source: str = "poe") -> list[dict]:
    payloads: list[dict] = []
    for tag in resolve_tag_codes(tag_codes):
        payloads.append(
            {
                "category_code": tag.category_code,
                "category_label": tag.category_label,
                "category_label_en": tag.category_label_en,
                "tag_code": tag.tag_code,
                "tag_label": tag.tag_label,
                "tag_label_en": tag.tag_label_en,
                "tag_path": tag.path,
                "tag_path_en": tag.path_en,
                "source": source,
            }
        )
    return payloads


def _compact_text(value: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", (value or "")).strip()
    if len(compact) <= max_chars:
        return compact
    trimmed = compact[:max_chars].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0]
    return trimmed
