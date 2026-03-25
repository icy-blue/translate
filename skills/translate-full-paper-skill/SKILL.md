---
name: translate-full-paper-skill
description: Produce full-paper translation messages by running initial prompt plus optional continuation loop against Poe, without writing any local DB records.
metadata:
  short-description: Translate whole paper to message blocks
---

# Translate Full Paper Skill

## Trigger
Use after bootstrap when you have a Poe attachment and need planner-driven unit translation output before persistence.

## Input
- `api_key` (required)
- `poe_model` (optional)
- `initial_prompt` (required planner prompt)
- `continue_count` (optional, default `0`)
- `poe_attachment` (`url/content_type/name`, required)

## Output
- `messages` (ordered user/bot turns)
- `first_bot_message`
- `continue_count_used`
- `translation_plan`
- `translation_status`

## Steps
1. Send planner prompt with attachment and parse the unit plan.
2. Translate the first unit and append the user/bot turn.
3. Loop `continue_count` times using the latest canonical unit `translation_status`.
4. Return full message list and the latest planner/unit status.

## Failure Handling
- Planner call fails: `error.code=planner_failed`.
- Initial translation call fails: `error.code=initial_translate_failed`.
- Continue loop failure: stop loop, return partial messages with warning.

## References
- Read `references/contracts.md` for payload shape.

## Script
- Entry: `scripts/run.py`
