#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import desc
from sqlmodel import Session, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.core.database import engine
from backend.domains.message_kinds import BOT_MESSAGE_KIND, infer_message_kind, role_from_message_kind
from backend.persistence.models import Message


TRANSLATION_STATUS_PATTERN = re.compile(
    r"\[TRANSLATION_STATUS\]\s*(.*?)\s*\[/TRANSLATION_STATUS\]",
    re.DOTALL,
)
SEPARATOR_LINE_PATTERN = re.compile(r"^\s*[-*_—]{3,}\s*$")
TRANSLATION_STATE_FULL_MARKERS = (
    "全文已经翻译完成",
    "全文已翻译完成",
    "全文翻译完成",
    "已全部翻译完成",
    "翻译已完成",
    "已完成全文翻译",
    "全文翻译结束",
    "全文翻译完毕",
    "论文已全部翻译完毕",
    "论文翻译完成",
    "论文翻译完毕",
    "本文翻译至此结束",
    "（全文完）",
)
TRANSLATION_STATE_BODY_MARKERS = (
    "正文翻译完毕",
    "正文部分翻译完毕",
    "全文主文部分翻译完成",
    "论文主体翻译完成",
    "主文部分翻译完成",
    "论文正文翻译完毕",
    "论文正文已结束",
    "论文正文已全部翻译完成",
    "正文已完成",
)
LEGACY_OPTIONAL_SECTION_PATTERNS = {
    "appendix": re.compile(r"^(?:#{1,6}\s*)?(?:附录|appendix|supplement(?:ary|al)?(?:\s+material)?)\b", re.IGNORECASE),
    "acknowledgements": re.compile(r"^(?:#{1,6}\s*)?(?:致谢|acknowledg(?:e)?ments?)\b", re.IGNORECASE),
    "references": re.compile(r"^(?:#{1,6}\s*)?(?:参考文献|references?|bibliography)\b", re.IGNORECASE),
}
DEFAULT_REPORT_PATH = ROOT_DIR / "data" / "legacy_display_filter_backfill_plan.jsonl"


@dataclass
class AuditRow:
    message_id: int
    conversation_id: str
    role: str
    created_at: str
    action: str
    visible_after_filter: bool
    reasons: list[str]
    has_status_block: bool
    original_length: int
    display_length: int
    original_content: str
    display_content: str


@dataclass
class FilterDecision:
    display_content: str
    reasons: list[str]
    has_status_block: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit or backfill legacy display-filter decisions for message visibility/content."
    )
    parser.add_argument(
        "--format",
        choices=("deleted", "diff", "jsonl"),
        default="jsonl",
        help="Output format. 'jsonl' matches backfill report shape; 'deleted' and 'diff' are audit-oriented views.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"Output path. Use '-' to print to stdout. Default: {DEFAULT_REPORT_PATH}",
    )
    parser.add_argument("--conversation-id", help="Only inspect messages from a single conversation.")
    parser.add_argument("--message-id", type=int, help="Only inspect one message row.")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N matched rows.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N matched rows.")
    parser.add_argument(
        "--order",
        choices=("asc", "desc"),
        default="asc",
        help="Message id ordering for matched rows.",
    )
    parser.add_argument(
        "--include-non-bot",
        action="store_true",
        help="Include user/system rows too. Default only inspects bot messages.",
    )
    parser.add_argument(
        "--context",
        type=int,
        default=2,
        help="Unified diff context line count when --format=diff.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Actually commit updates to the database. trim rows rewrite content; hide rows set visible_to_user=false.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Commit every N updated rows when --write is enabled.",
    )
    return parser.parse_args()


def _is_separator_line(line: str) -> bool:
    return bool(SEPARATOR_LINE_PATTERN.match(line or ""))


def _normalize_legacy_hint_text(line: str) -> str:
    text = (line or "").strip()
    if not text:
        return ""
    normalized = text.replace("**", "").replace("__", "")
    normalized = re.sub(r"^[\s\u2705\u2714\u2713\u2611✅✔✓☑]+", "", normalized)
    return normalized.strip()


def _is_legacy_completion_marker_line(line: str) -> bool:
    normalized = _normalize_legacy_hint_text(line)
    if not normalized:
        return False
    return any(marker in normalized for marker in (*TRANSLATION_STATE_FULL_MARKERS, *TRANSLATION_STATE_BODY_MARKERS))


def _is_legacy_completion_cta_line(line: str) -> bool:
    normalized = _normalize_legacy_hint_text(line)
    if not normalized:
        return False
    if normalized in {"如需", "如需:", "如需："}:
        return True
    if normalized.startswith(
        (
            "如果你希望我",
            "如果你希望",
            "如果你需要我",
            "如果你需要",
            "如果需要",
            "可以直接告诉我",
            "直接告诉我",
            "告诉我即可",
            "我可以继续协助",
            "我可以继续帮你",
        )
    ):
        return True
    return False


def _is_legacy_continue_hint_line(line: str) -> bool:
    normalized = _normalize_legacy_hint_text(line)
    if not normalized:
        return False
    lower = normalized.lower()
    if "如需继续" in normalized:
        return True
    if normalized.startswith(("下一章", "下一节", "（下一章", "(下一章", "（下一节", "(下一节")):
        return True
    if ("下一章" in normalized or "下一节" in normalized) and ("继续" in normalized or "reply" in lower or "continue" in lower):
        return True
    if "当你输入" in normalized and "继续" in normalized:
        return True
    if "如果你说" in normalized and "继续" in normalized:
        return True
    if "是否继续" in normalized:
        return True
    if ("请回复" in normalized or "请输入" in normalized or "请继续输入" in normalized or "回复" in normalized) and "继续" in normalized:
        return True
    if "翻译下一章" in normalized or "翻译下一节" in normalized:
        return True
    if "reply" in lower and "continue" in lower:
        return True
    return False


def _is_legacy_completion_service_line(line: str) -> bool:
    normalized = _normalize_legacy_hint_text(line)
    if not normalized:
        return False
    lower = normalized.lower()
    if _is_legacy_completion_marker_line(normalized):
        return True
    if _is_legacy_completion_cta_line(normalized):
        return True
    service_keywords = (
        "latex",
        "word",
        "伪代码",
        "ppt",
        "ppt大纲",
        "答辩ppt",
        "答辩讲稿",
        "论文笔记",
        "精读笔记",
        "方法解析",
        "方法对比分析",
        "方法对比总结",
        "技术解读文章",
        "技术解读",
        "技术报告",
        "学术润色",
        "中文版论文格式",
        "可投稿中文版本",
        "排版整理",
        "排版版翻译稿",
        "公式优化",
        "推导公式讲解",
        "继续协助",
        "可以告诉我",
        "告诉我你想",
        "告诉我你接下来想做什么",
        "告诉我你想要哪种版本",
        "如需整理",
        "如需翻译参考文献",
        "如需翻译",
        "整理为",
        "整理成",
        "可直接发表",
        "总结全文",
        "补充材料",
        "如果你希望",
        "如果你需要",
        "请告诉我你的需求",
        "请告诉我",
        "你的需求",
        "翻译致谢",
        "继续翻译「致谢」",
        "继续翻译致谢",
        "继续翻译附录",
        "继续翻译参考文献",
        "参考文献标题",
        "翻译全部参考文献标题",
        "提炼创新点",
        "术语对照表",
        "方法流程图",
        "流程图",
        "深入处理",
        "输出为word",
        "导出为word",
        "逐段对照",
        "英文+中文",
        "中英对照",
        "精简版讲稿",
        "生成精简版讲稿",
        "讲稿版精简稿",
    )
    if any(keyword in lower for keyword in ("latex", "ppt", "appendix", "word")):
        return True
    if any(keyword in normalized for keyword in service_keywords if keyword not in {"latex", "ppt"}):
        return True
    return False


def _is_legacy_completion_service_bullet_payload(payload: str) -> bool:
    normalized = _normalize_legacy_hint_text(payload)
    if not normalized:
        return False
    if _is_legacy_completion_service_line(normalized):
        return True
    action_prefixes = (
        "导出为",
        "生成",
        "整理成",
        "整理为",
        "提炼为",
        "提炼成",
        "总结",
        "解释",
        "讲解",
        "画",
        "绘制",
        "制作",
        "转成",
        "输出为",
        "梳理",
        "归纳",
        "或需要我",
        "需要我",
    )
    if normalized.startswith(action_prefixes):
        return True
    return False


def _is_legacy_service_bullet_line(line: str) -> bool:
    text = (line or "").strip()
    if not re.match(r"^(?:[-*•]\s+|\d+[.)]\s+)", text):
        return False
    payload = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", text).strip()
    return _is_legacy_completion_service_line(payload)


def _is_legacy_preview_heading_line(line: str) -> bool:
    text = (line or "").strip()
    if not text:
        return True
    if _is_separator_line(text):
        return True
    if re.match(r"^#{1,6}\s+\S+", text):
        return True
    if re.match(r"^(?:第\s*[0-9一二三四五六七八九十IVXLC]+\s*[章节]|[IVXLC0-9]+[\.、．)]\s*\S+)", text, re.IGNORECASE):
        return True
    return False


def _is_legacy_completion_service_block(lines: list[str]) -> bool:
    if not lines:
        return False

    has_completion_marker = False
    has_footer_signal = False
    for line in lines:
        stripped = line.strip()
        if not stripped or _is_separator_line(stripped):
            continue
        if _is_legacy_completion_marker_line(stripped):
            has_completion_marker = True
            has_footer_signal = True
            continue
        if _is_legacy_continue_hint_line(stripped) or _is_legacy_completion_service_line(stripped):
            has_footer_signal = True
            continue
        if re.match(r"^(?:[-*•]\s+|\d+[.)]\s+)", stripped):
            payload = re.sub(r"^(?:[-*•]\s+|\d+[.)]\s+)", "", stripped).strip()
            if _is_legacy_completion_service_bullet_payload(payload):
                has_footer_signal = True
                continue
        if _is_legacy_preview_heading_line(stripped) and has_footer_signal:
            continue
        return False

    return has_completion_marker and has_footer_signal


def _find_legacy_optional_section_line_index(lines: list[str]) -> tuple[int | None, str | None]:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or _is_separator_line(stripped):
            continue
        for scope, pattern in LEGACY_OPTIONAL_SECTION_PATTERNS.items():
            if pattern.match(stripped):
                return index, scope
    return None, None


def _strip_translation_status_block(content: str | None) -> str:
    return TRANSLATION_STATUS_PATTERN.sub("", content or "").strip()


def _strip_legacy_bot_footer_with_reasons(content: str | None) -> tuple[str, list[str]]:
    text = (content or "").strip()
    reasons: list[str] = []
    if not text:
        return "", reasons

    lines = text.splitlines()
    removed_tail = False
    while lines:
        tail = lines[-1].strip()
        if (
            not tail
            or _is_separator_line(tail)
            or _is_legacy_continue_hint_line(tail)
            or _is_legacy_completion_service_line(tail)
            or _is_legacy_service_bullet_line(tail)
        ):
            removed_tail = True
            lines.pop()
            continue
        break
    while lines and (not lines[-1].strip() or _is_separator_line(lines[-1].strip())):
        removed_tail = True
        lines.pop()

    if removed_tail:
        reasons.append("trimmed_legacy_footer")

    trailing_block_index = None
    for index, line in enumerate(lines):
        is_footer_lead = (
            _is_legacy_continue_hint_line(line)
            or _is_legacy_completion_service_line(line)
            or _is_legacy_service_bullet_line(line)
        )
        if not is_footer_lead and not _is_legacy_completion_service_block(lines[index:]):
            continue

        trailing_lines = lines[index:]
        if trailing_lines and (
            all(
                _is_legacy_continue_hint_line(line)
                or _is_legacy_completion_service_line(line)
                or _is_legacy_service_bullet_line(line)
                or _is_legacy_preview_heading_line(line)
                for line in trailing_lines
            )
            or _is_legacy_completion_service_block(trailing_lines)
        ):
            trailing_block_index = index
            break

    if trailing_block_index is not None:
        while trailing_block_index > 0:
            previous = lines[trailing_block_index - 1].strip()
            if previous and not _is_separator_line(previous):
                break
            trailing_block_index -= 1
        lines = lines[:trailing_block_index]
        while lines and (not lines[-1].strip() or _is_separator_line(lines[-1].strip())):
            lines.pop()
        reasons.append("trimmed_trailing_service_block")

    return "\n".join(lines).strip(), reasons


def apply_current_display_strategy(content: str | None) -> FilterDecision:
    original = content or ""
    has_status_block = bool(TRANSLATION_STATUS_PATTERN.search(original))
    stripped = _strip_translation_status_block(original)
    reasons: list[str] = []

    if not stripped:
        return FilterDecision(display_content="", reasons=["empty_after_status_strip"], has_status_block=has_status_block)

    if not has_status_block:
        stripped, footer_reasons = _strip_legacy_bot_footer_with_reasons(stripped)
        reasons.extend(footer_reasons)
        lines = stripped.splitlines()
        optional_section_index, optional_scope = _find_legacy_optional_section_line_index(lines)
        if optional_section_index == 0:
            reasons.append(f"hidden_optional_section_only:{optional_scope}")
            return FilterDecision(display_content="", reasons=reasons, has_status_block=has_status_block)
        if optional_section_index is not None:
            while optional_section_index > 0:
                previous = lines[optional_section_index - 1].strip()
                if previous and not _is_separator_line(previous):
                    break
                optional_section_index -= 1
            stripped = "\n".join(lines[:optional_section_index]).rstrip()
            reasons.append(f"trimmed_optional_section_tail:{optional_scope}")

    stripped = stripped.strip()
    if not stripped:
        reasons.append("empty_after_cleanup")
    return FilterDecision(display_content=stripped, reasons=reasons, has_status_block=has_status_block)


def build_statement(args: argparse.Namespace):
    order_column = desc(Message.id) if args.order == "desc" else Message.id
    statement = select(Message).order_by(order_column)

    if not args.include_non_bot:
        statement = statement.where(Message.message_kind == BOT_MESSAGE_KIND)
    if args.conversation_id:
        statement = statement.where(Message.conversation_id == args.conversation_id)
    if args.message_id is not None:
        statement = statement.where(Message.id == args.message_id)
    if args.offset:
        statement = statement.offset(max(0, args.offset))
    if args.limit is not None:
        statement = statement.limit(max(0, args.limit))
    return statement


def build_audit_row(message: Message) -> AuditRow:
    message_kind = infer_message_kind(message_kind=getattr(message, "message_kind", None), content=message.content)
    role = role_from_message_kind(message_kind)
    if role != "bot":
        original_content = message.content or ""
        return AuditRow(
            message_id=message.id,
            conversation_id=message.conversation_id,
            role=role,
            created_at=str(message.created_at),
            action="keep",
            visible_after_filter=True,
            reasons=["non_bot_message"],
            has_status_block=False,
            original_length=len(original_content),
            display_length=len(original_content),
            original_content=original_content,
            display_content=original_content,
        )

    original_content = message.content or ""
    decision = apply_current_display_strategy(original_content)
    display_content = decision.display_content

    if not display_content:
        action = "hide"
        visible = False
    elif display_content == original_content:
        action = "keep"
        visible = True
    else:
        action = "trim"
        visible = True

    return AuditRow(
        message_id=message.id,
        conversation_id=message.conversation_id,
        role=role,
        created_at=str(message.created_at),
        action=action,
        visible_after_filter=visible,
        reasons=decision.reasons,
        has_status_block=decision.has_status_block,
        original_length=len(original_content),
        display_length=len(display_content),
        original_content=original_content,
        display_content=display_content,
    )


def _render_deleted_line(line: str) -> str:
    return line if line else "[blank line]"


def _get_deleted_lines(original: str, display: str) -> list[str]:
    deleted_lines: list[str] = []
    diff_lines = difflib.ndiff(original.splitlines(), display.splitlines())
    for line in diff_lines:
        if line.startswith("- "):
            deleted_lines.append(line[2:])
    return deleted_lines


def _render_deleted_view(rows: list[AuditRow]) -> str:
    blocks: list[str] = []
    for row in rows:
        deleted_lines = _get_deleted_lines(row.original_content, row.display_content)
        if row.action == "hide" and not deleted_lines:
            deleted_lines = row.original_content.splitlines()
        if row.action == "keep":
            continue
        header = [
            f"message_id={row.message_id} conversation_id={row.conversation_id} action={row.action}",
            f"reasons={','.join(row.reasons) if row.reasons else '-'}",
        ]
        body = deleted_lines or ["[no deleted lines captured]"]
        blocks.append("\n".join(header + ["deleted:"] + [f"- {_render_deleted_line(line)}" for line in body]))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _render_diff_view(rows: list[AuditRow], context: int) -> str:
    blocks: list[str] = []
    for row in rows:
        if row.action == "keep":
            continue
        diff_lines = list(
            difflib.unified_diff(
                row.original_content.splitlines(),
                row.display_content.splitlines(),
                fromfile=f"message:{row.message_id}:original",
                tofile=f"message:{row.message_id}:display",
                n=max(0, context),
                lineterm="",
            )
        )
        if not diff_lines:
            diff_lines = ["[no diff lines captured]"]
        header = f"# message_id={row.message_id} conversation_id={row.conversation_id} action={row.action} reasons={','.join(row.reasons) if row.reasons else '-'}"
        blocks.append("\n".join([header] + diff_lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _render_jsonl(rows: list[AuditRow]) -> str:
    return "".join(json.dumps(asdict(row), ensure_ascii=False) + "\n" for row in rows)


def _write_audit_rows(rows: list[AuditRow], output: Path | str, output_format: str, context: int) -> None:
    if output_format == "jsonl":
        rendered = _render_jsonl(rows)
    elif output_format == "diff":
        rendered = _render_diff_view(rows, context)
    else:
        rendered = _render_deleted_view(rows)

    if output == "-":
        sys.stdout.write(rendered)
        return

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write(rendered)


def _derive_action_output_path(output: Path | str, action: str) -> Path | str:
    if output == "-":
        return "-"
    output_path = Path(output)
    return output_path.with_name(f"{output_path.stem}.{action}{output_path.suffix}")


def _write_split_audit_outputs(rows: list[AuditRow], output: Path | str, output_format: str, context: int) -> None:
    action_rows = {
        "trim": [row for row in rows if row.action == "trim"],
        "hide": [row for row in rows if row.action == "hide"],
    }
    if output == "-":
        for action in ("trim", "hide"):
            sys.stdout.write(f"## {action}\n")
            _write_audit_rows(action_rows[action], "-", output_format, context)
            sys.stdout.write("\n")
        return

    for action in ("trim", "hide"):
        _write_audit_rows(action_rows[action], _derive_action_output_path(output, action), output_format, context)


def _write_backfill_report(rows: list[dict[str, object]], output: Path | str) -> None:
    if output == "-":
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        return

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    args = parse_args()

    with Session(engine) as session:
        messages = session.exec(build_statement(args)).all()

        audit_rows = [build_audit_row(message) for message in messages]
        affected_rows = [row for row in audit_rows if row.action in {"trim", "hide"}]

        if not args.write:
            _write_split_audit_outputs(affected_rows, "-" if str(args.output) == "-" else args.output, args.format, args.context)
            destination = "stdout" if str(args.output) == "-" else str(args.output)
            print(
                f"AUDIT: affected={len(affected_rows)}, trim={sum(1 for row in affected_rows if row.action == 'trim')}, hide={sum(1 for row in affected_rows if row.action == 'hide')}. Output base -> {destination}",
                file=sys.stderr,
            )
            return 0

        updated = 0
        report_rows: list[dict[str, object]] = []
        for row, message in zip(audit_rows, messages):
            if row.action == "keep":
                continue

            next_content = message.content
            next_visible = bool(getattr(message, "visible_to_user", True))
            if row.action == "trim":
                next_content = row.display_content
            elif row.action == "hide":
                next_visible = False

            report_rows.append(
                {
                    "message_id": message.id,
                    "conversation_id": message.conversation_id,
                    "action": row.action,
                    "reasons": row.reasons,
                    "visible_before": bool(getattr(message, "visible_to_user", True)),
                    "visible_after": next_visible,
                    "content_changed": getattr(message, "content", "") != next_content,
                }
            )

            changed = False
            if getattr(message, "content", "") != next_content:
                message.content = next_content
                changed = True
            if getattr(message, "visible_to_user", None) != next_visible:
                message.visible_to_user = next_visible
                changed = True
            if changed:
                session.add(message)
                updated += 1
                if updated % max(1, args.batch_size) == 0:
                    session.commit()

        if updated % max(1, args.batch_size) != 0:
            session.commit()

    _write_backfill_report(report_rows, "-" if str(args.output) == "-" else args.output)
    destination = "stdout" if str(args.output) == "-" else str(args.output)
    print(
        f"WRITE: affected={len(report_rows)}, updated={updated}, report={destination}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
