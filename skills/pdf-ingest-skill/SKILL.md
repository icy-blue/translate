---
name: pdf-ingest-skill
description: Read a single PDF from path or base64, validate input, compute SHA256 fingerprint, and optionally check duplicate conversations in DB. Use this before any other paper-processing skill.
metadata:
  short-description: Validate PDF and compute fingerprint
---

# PDF Ingest Skill

## Trigger
Use when you need the first step of the pipeline: read PDF bytes, validate minimal inputs, compute fingerprint, and check existing conversation by fingerprint.

## Input
- `file_path` or `file_bytes_base64` (required, one of them)
- `filename` (optional, inferred from path when possible)
- `check_existing` (optional, default `true`)

## Output
- `filename`
- `file_size`
- `file_bytes_base64`
- `fingerprint`
- `is_existing`
- `existing_conversation_id`

## Steps
1. Load JSON input from `--input-json`.
2. Read bytes from `file_path` or decode `file_bytes_base64`.
3. Compute SHA256 fingerprint.
4. If `check_existing=true`, query DB by fingerprint.
5. Write output JSON to `--output-json`.

## Failure Handling
- Missing inputs: return structured `ok=false` with `error.code=invalid_input`.
- File read/decode error: `error.code=read_failed`.
- DB check error: keep processing output with `is_existing=false` and attach warning in `errors`.

## References
- Read `references/contracts.md` for exact I/O schema.

## Script
- Entry: `scripts/run.py`
- Usage: `python skills/pdf-ingest-skill/scripts/run.py --input-json in.json --output-json out.json`
