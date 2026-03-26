---
name: persist-pipeline-bundle-skill
description: Persist a fully prepared PipelineBundle by calling backend POST /agent/pipeline/commits with x-agent-token.
metadata:
  short-description: Submit bundle to backend commit API
---

# Persist Pipeline Bundle Skill

## Trigger
Use as the only side-effect step after all processing is complete.

## Input
- `base_url` (required)
- `agent_token` (required)
- `bundle` (required)

## Output
- backend response (`status/exists/conversation_id/committed_parts/errors`)

## Steps
1. Validate bundle exists.
2. Send HTTP POST to `/agent/pipeline/commits` with `x-agent-token`.
3. Parse JSON response and return.

## Failure Handling
- HTTP 4xx/5xx: `ok=false` + `error.code=persist_failed`.
- Network timeout: `ok=false` + `error.code=network_failed`.

## References
- Read `references/contracts.md` for expected response fields.
