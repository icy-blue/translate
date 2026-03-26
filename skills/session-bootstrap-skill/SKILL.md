---
name: session-bootstrap-skill
description: Upload PDF attachment to Poe and extract paper title for a new session shell. This skill is side-effect free for local database and only returns bootstrap data.
metadata:
  short-description: Upload attachment and extract title
---

# Session Bootstrap Skill

## Trigger
Use after `pdf-ingest-skill` when PDF bytes are available and you need Poe attachment metadata + extracted title.

## Input
- `filename` (required)
- `file_bytes_base64` (required)
- `api_key` (required)
- `title_model` (optional, default `GPT-5.2-Instant`)

## Output
- `conversation_id`
- `file_id`
- `poe_attachment` (`url/content_type/name`)
- `title`
- `file_record` (metadata draft, no DB write)

## Steps
1. Decode input bytes and upload original PDF to Poe.
2. Build first-page PDF and upload it for title extraction.
3. Request title model to extract title, fallback to filename.
4. Return all bootstrap fields.

## Failure Handling
- Poe upload failure: `error.code=poe_upload_failed`.
- Title extraction failure: fallback to filename and include warning.

## References
- Read `references/contracts.md` for exact request/response fields.

## Script
- Entry: `scripts/run.py`
