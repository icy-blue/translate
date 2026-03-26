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

## Artifact Builder Input

To avoid generating ad hoc Python wrappers, `scripts/run.py` also supports a fixed builder mode:

```json
{
  "mode": "build_artifact",
  "translation_plan": {
    "status": "ok",
    "units": ["ABSTRACT", "1 Introduction"],
    "appendix_units": [],
    "reason": ""
  },
  "unit_results": [
    {
      "unit_id": "ABSTRACT",
      "state": "OK",
      "content": "# 摘要\n..."
    },
    {
      "unit_id": "1 Introduction",
      "state": "OK",
      "content": "# 1 引言\n..."
    }
  ],
  "errors": []
}
```

`unit_results` must follow the planner order exactly and stop at the first `UNSUPPORTED` unit.

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

## Markdown Renderer Input

The human-readable Markdown renderer accepts:

```bash
python skills/self-translate-full-paper-skill/scripts/render_markdown.py \
  --input /absolute/path/to/paper.self_translate.json \
  --output /absolute/path/to/paper.self_translate.md \
  --title "Paper Translation" \
  --source-pdf "/absolute/path/to/paper.pdf"
```

It reads a standard self-translate artifact and renders:

- document title and source label
- translation summary
- translation plan summary
- translated sections in order
- warnings if present
