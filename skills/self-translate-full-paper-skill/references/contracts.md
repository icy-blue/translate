# Contracts

## Agent-Native Translation Input

- `file_path`: required absolute local PDF path
- `filename`: optional display filename
- `continue_count`: optional number of additional units to translate after the first translated unit

## Agent-Native Translation Output

The agent-native result must match the existing `translate-full-paper-skill` top-level contract:

- `messages`: ordered user and bot turns
- `first_bot_message`: cleaned first bot content
- `continue_count_used`: count of continuation turns actually translated after the first translated unit
- `translation_plan`: canonical planner payload
- `translation_status`: canonical latest status payload
- `errors`: warning list, usually empty

## Message Contract

### User messages

- `role="user"`
- first hidden trigger must use `message_kind="system_prompt"`
- later hidden triggers must use `message_kind="continue_command"`
- `visible_to_user=false`

### Bot messages

- `role="bot"`
- `message_kind="bot_reply"`
- `visible_to_user=true`
- `content` must be cleaned for display and must not contain raw `[TRANSLATION_STATUS_JSON]`
- `client_payload.translation_plan` must be canonical
- `client_payload.translation_status` must be canonical

## Bridge Runner Input

The compatibility bridge in `scripts/run.py` accepts:

```json
{
  "agent_output_json": "/absolute/path/to/self_translate_result.json"
}
```

`agent_output_json` must point to a JSON artifact produced by an executing agent that followed this skill.

## Bridge Runner Output

The bridge returns the same JSON shape as the existing `translate-full-paper-skill` runner:

```json
{
  "ok": true,
  "messages": [],
  "first_bot_message": "",
  "continue_count_used": 0,
  "translation_plan": {},
  "translation_status": {},
  "errors": []
}
```
