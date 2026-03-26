---
name: translate-full-paper-skill
description: Produce pipeline-commit-ready full-paper translation messages that match backend ingest plus continue-translation behavior, without local DB writes.
metadata:
  short-description: Build backend-compatible full-paper translation messages
---

# Translate Full Paper Skill

## Trigger
Use after bootstrap when you have a Poe PDF attachment and need side-effect-free translation output that can later be persisted with the same message semantics as the backend.

## Input
- `api_key` (required)
- `poe_model` (optional)
- `initial_prompt` (optional planner override; defaults to backend `settings.initial_prompt`)
- `continue_count` (optional, default `0`)
- `poe_attachment` (`url/content_type/name`, required)

## Output
- `messages` (ordered user/bot turns, already normalized for `/agent/pipeline/commits`)
- `first_bot_message` (cleaned first bot content, without translation status block)
- `continue_count_used`
- `translation_plan`
- `translation_status`

## Steps
1. Send the planner prompt and build canonical `translation_plan`.
2. Mirror backend ingest behavior:
   - unsupported plan: emit one hidden `system_prompt` user message plus one bot reply carrying canonical payloads
   - supported plan: translate the first body unit and mark that hidden user message as `system_prompt`
3. Loop `continue_count` times using canonical `translation_status`, switching to appendix units automatically when body units are done.
4. For every bot reply, strip `[TRANSLATION_STATUS_JSON]` from visible content and keep canonical `translation_plan` / `translation_status` in `client_payload`.
5. Return the ordered message list plus the latest canonical status.

## Failure Handling
- Planner call fails: `error.code=planner_failed`.
- Initial translation call fails: `error.code=initial_translate_failed`.
- Continue loop failure: stop loop, return partial messages with warning.

## References
- Read `references/contracts.md` for the pipeline-facing request and response shape.

## Script
- Entry: `scripts/run.py`
