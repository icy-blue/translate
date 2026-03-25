from __future__ import annotations

from typing import Any


def build_command_block(action: str, target_scope: str) -> str:
    return f"[COMMAND]\naction={action}\ntarget={target_scope}\n[/COMMAND]"


def build_input_status_block(status: dict[str, Any]) -> str:
    return (
        "[INPUT_STATUS]\n"
        f"scope={str(status.get('scope', '')).strip()}\n"
        f"completed={str(status.get('completed', '')).strip()}\n"
        f"current={str(status.get('current', '')).strip()}\n"
        f"next={str(status.get('next', '')).strip()}\n"
        f"remaining={str(status.get('remaining', '')).strip()}\n"
        f"state={str(status.get('state', '')).strip()}\n"
        f"phase={str(status.get('phase', '')).strip()}\n"
        "[/INPUT_STATUS]"
    )


def build_initial_translation_prompt(template: str) -> str:
    return str(template or "").strip()


def build_continue_translation_prompt(
    template: str,
    *,
    translation_status: dict[str, Any],
    action: str,
    target_scope: str,
) -> str:
    command_block = build_command_block(action=action, target_scope=target_scope)
    input_status_block = build_input_status_block(translation_status)
    prompt = str(template or "").strip()
    return prompt.replace("<<INPUT_STATUS_BLOCK>>", input_status_block).replace("<<COMMAND_BLOCK>>", command_block).strip()
