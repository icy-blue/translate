---
name: translate-full-paper-skill
description: Produce full-paper translation messages by running initial prompt plus optional continuation loop against Poe, without writing any local DB records.
metadata:
  short-description: Translate whole paper to message blocks
---

# Translate Full Paper Skill

## Trigger
Use after bootstrap when you have a Poe attachment and need full translation output before persistence.

## Input
- `api_key` (required)
- `poe_model` (optional)
- `initial_prompt` (required)
- `continue_count` (optional, default `0`)
- `continue_message` (optional, default `继续`)
- `poe_attachment` (`url/content_type/name`, required)

## Output
- `messages` (ordered user/bot turns)
- `first_bot_message`
- `continue_count_used`

## Steps
1. Send initial prompt with attachment.
2. Append bot response.
3. Loop `continue_count` times using `continue_message`.
4. Return full message list.

## Failure Handling
- Initial call fails: `error.code=initial_translate_failed`.
- Continue loop failure: stop loop, return partial messages with warning.

## References
- Read `references/contracts.md` for payload shape.

## Script
- Entry: `scripts/run.py`
