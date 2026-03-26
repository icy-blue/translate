---
name: compose-pipeline-bundle-skill
description: Compose a normalized PipelineBundle object from upstream skill outputs to prepare a single commit payload.
metadata:
  short-description: Assemble final PipelineBundle
---

# Compose Pipeline Bundle Skill

## Trigger
Use after all processing skills complete and before persistence.

## Input
- `title` (required)
- `file_record` (required; must include `filename`, `fingerprint`, `poe_url`)
- `messages` (required)
- `figures/tables/tags/meta/errors` (optional)
- `conversation_id` (optional)

## Output
- `bundle` object matching backend `/agent/pipeline/commits` contract.

## Steps
1. Validate required top-level fields.
2. Validate `file_record.filename`, `file_record.fingerprint`, and `file_record.poe_url`.
3. Fill default `content_type` / `poe_name` when omitted.
4. Normalize missing arrays to empty lists.
5. Normalize optional `meta` and `errors`.
6. Emit `bundle`.

## Failure Handling
- Invalid required field: `ok=false` + `error.code=invalid_bundle`.

## References
- Read `references/contracts.md` for exact bundle shape.
