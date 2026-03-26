#!/usr/bin/env python3
"""Render a self-translation artifact JSON into a human-readable Markdown document."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_payload(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Input payload must be a JSON object.")
    return data


def require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Field '{key}' must be a list.")
    return value


def require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Field '{key}' must be an object.")
    return value


def _count_translated_units(messages: list[Any]) -> int:
    count = 0
    for item in messages:
        if not isinstance(item, dict):
            continue
        if str(item.get("role", "")).strip().lower() != "bot":
            continue
        if str(item.get("content", "")).strip():
            count += 1
    return count


def _render_status_summary(translation_status: dict[str, Any]) -> list[str]:
    lines = [
        f"- 当前状态: {translation_status.get('state', '')}",
        f"- 当前 scope: {translation_status.get('active_scope', '')}",
        f"- 当前 unit: {translation_status.get('current_unit_id', '') or '-'}",
        f"- 下一 unit: {translation_status.get('next_unit_id', '') or '-'}",
        f"- 已完成 unit 数: {translation_status.get('completed_unit_count', 0)} / {translation_status.get('total_unit_count', 0)}",
    ]
    reason = str(translation_status.get("reason", "")).strip()
    if reason:
        lines.append(f"- 原因: {reason}")
    return lines


def _render_plan_summary(translation_plan: dict[str, Any]) -> list[str]:
    units = translation_plan.get("units") if isinstance(translation_plan.get("units"), list) else []
    appendix_units = translation_plan.get("appendix_units") if isinstance(translation_plan.get("appendix_units"), list) else []
    lines = [
        f"- Planner 状态: {translation_plan.get('status', '')}",
        f"- 主体 unit: {len(units)}",
        f"- 附录 unit: {len(appendix_units)}",
    ]
    if units:
        lines.append(f"- 主体顺序: {', '.join(str(item) for item in units)}")
    if appendix_units:
        lines.append(f"- 附录顺序: {', '.join(str(item) for item in appendix_units)}")
    reason = str(translation_plan.get("reason", "")).strip()
    if reason:
        lines.append(f"- Planner 原因: {reason}")
    return lines


def _render_messages(messages: list[Any]) -> list[str]:
    rendered: list[str] = []
    visible_index = 0
    for item in messages:
        if not isinstance(item, dict):
            raise ValueError("Each message must be an object.")
        role = str(item.get("role", "")).strip().lower()
        if role != "bot":
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        visible_index += 1
        payload = item.get("client_payload") if isinstance(item.get("client_payload"), dict) else {}
        translation_status = payload.get("translation_status") if isinstance(payload.get("translation_status"), dict) else {}
        unit_id = str(translation_status.get("current_unit_id", "")).strip() or f"section-{visible_index}"
        rendered.append(f"## {visible_index}. {unit_id}")
        rendered.append("")
        rendered.append(content)
        rendered.append("")
    return rendered


def _render_errors(errors: list[Any]) -> list[str]:
    if not errors:
        return []
    lines = ["## Warnings", ""]
    for index, item in enumerate(errors, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Error #{index} must be an object.")
        skill = str(item.get("skill", "")).strip() or "-"
        error_type = str(item.get("type", "")).strip() or "-"
        message = str(item.get("message", "")).strip() or "-"
        retryable = "yes" if bool(item.get("retryable", False)) else "no"
        lines.append(f"- [{index}] skill={skill}; type={error_type}; retryable={retryable}; message={message}")
    lines.append("")
    return lines


def render_markdown(
    payload: dict[str, Any],
    *,
    title: str | None = None,
    source_pdf: str | None = None,
) -> str:
    messages = require_list(payload, "messages")
    translation_plan = require_dict(payload, "translation_plan")
    translation_status = require_dict(payload, "translation_status")
    errors = require_list(payload, "errors")

    rendered_title = (title or "").strip() or "Paper Translation"
    lines = [f"# {rendered_title}", ""]
    if source_pdf and source_pdf.strip():
        lines.append(f"- 源 PDF: {source_pdf.strip()}")
    lines.append(f"- 可见译文段数: {_count_translated_units(messages)}")
    lines.append(f"- 续翻次数: {int(payload.get('continue_count_used', 0) or 0)}")
    lines.append(f"- 首段摘要预览: {str(payload.get('first_bot_message', '')).strip()[:120] or '-'}")
    lines.append("")
    lines.append("## Translation Summary")
    lines.append("")
    lines.extend(_render_status_summary(translation_status))
    lines.append("")
    lines.append("## Translation Plan")
    lines.append("")
    lines.extend(_render_plan_summary(translation_plan))
    lines.append("")
    lines.append("## Translation")
    lines.append("")
    lines.extend(_render_messages(messages))
    lines.extend(_render_errors(errors))
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a self-translate artifact into human-readable Markdown.")
    parser.add_argument("--input", required=True, help="Path to the self-translate JSON artifact.")
    parser.add_argument("--output", help="Path to the Markdown output. Defaults to stdout.")
    parser.add_argument("--title", help="Optional rendered Markdown title.")
    parser.add_argument("--source-pdf", help="Optional source PDF label or path.")
    args = parser.parse_args()

    payload = load_payload(Path(args.input))
    markdown = render_markdown(
        payload,
        title=args.title,
        source_pdf=args.source_pdf,
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8-sig")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
