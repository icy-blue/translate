# Contracts

## Input
- `api_key`: Poe API key.
- `poe_model`: optional override; defaults to backend `settings.poe_model`.
- `initial_prompt`: optional override for the planner prompt; defaults to backend `settings.initial_prompt`.
- `continue_count`: number of extra unit-translation turns after the first translated unit.
- `poe_attachment`: `{ "url", "content_type", "name" }`.

## Output
- `messages`: ordered message objects that can be committed through the pipeline API.
- User messages always include:
  - `role="user"`
  - `message_kind="system_prompt"` for the first hidden translation trigger
  - `message_kind="continue_command"` for later hidden continuation triggers
  - `visible_to_user=false`
- Bot messages always include:
  - `role="bot"`
  - `message_kind="bot_reply"`
  - `visible_to_user=true`
  - `content`: cleaned visible text with `[TRANSLATION_STATUS_JSON]` removed
  - `client_payload.translation_plan`
  - `client_payload.translation_status`

## Notes
- `first_bot_message` is the cleaned first bot reply used by tag extraction.
- Unsupported planner output still emits a backend-compatible hidden `system_prompt` plus an empty bot reply carrying canonical payloads.
