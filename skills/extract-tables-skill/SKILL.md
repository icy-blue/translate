---
name: extract-tables-skill
description: Extract tables from PDF bytes and emit serializable table payloads for later batch persistence.
metadata:
  short-description: Extract table payloads from PDF
---

# Extract Tables Skill

## Trigger
Use when table extraction is required before bundle persistence.

## Input
- `file_path` or `file_bytes_base64` (required)
- `preferred_direction` (optional: `above` or `below`)

## Output
- `tables[]` with `page_number/table_index/table_label/caption/image_mime_type/image_data_base64/image_width/image_height`

## Steps
1. Load PDF bytes.
2. Run `backend.domain.pdf_figures.extract_pdf_tables`.
3. Convert binary image bytes to base64 for JSON transport.
4. Return table list.

## Failure Handling
- Extraction failure returns `ok=false` with `error.code=extract_failed`.

## References
- Read `references/contracts.md` for field list.
