---
name: refresh-metadata-skill
description: Fetch Semantic Scholar match data and CCF mapping as a pure metadata payload, without writing local DB.
metadata:
  short-description: Fetch semantic metadata payload
---

# Refresh Metadata Skill

## Trigger
Use when metadata enrichment is enabled.

## Input
- `title` (required)
- `conversation_id` (optional; generated when missing)
- `semantic_api_key` (optional)

## Output
- `meta` payload aligned with `PaperSemanticScholarResult` fields.

## Steps
1. Call Semantic Scholar match API.
2. Build normalized payload via existing backend helper logic.
3. Return metadata.

## Failure Handling
- Network/API errors: `ok=false` with `error.code=metadata_failed`.

## References
- Read `references/contracts.md` for shape and optional fields.
