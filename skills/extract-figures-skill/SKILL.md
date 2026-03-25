---
name: extract-figures-skill
description: Extract figures from PDF bytes and emit serializable figure payloads for later batch persistence.
metadata:
  short-description: Extract figure payloads from PDF
---

# Extract Figures Skill

## Trigger
Use when figure extraction is required before bundle persistence.

## Input
- `file_path` or `file_bytes_base64` (required)
- `preferred_direction` (optional: `above` or `below`)

## Output
- `figures[]` with `page_number/figure_index/figure_label/caption/image_mime_type/image_data_base64/image_width/image_height`

## Steps
1. Load PDF bytes.
2. Run `backend.domain.pdf_figures.extract_pdf_figures`.
3. Convert binary image bytes to base64 for JSON transport.
4. Return figure list.

## Failure Handling
- Extraction failure returns `ok=false` with `error.code=extract_failed`.

## References
- Read `references/contracts.md` for field list.
