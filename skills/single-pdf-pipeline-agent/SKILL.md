---
name: single-pdf-pipeline-agent
description: Orchestrate all PDF pipeline skills in order, run extraction/enrichment in parallel, compose bundle, and persist exactly once.
metadata:
  short-description: Orchestrate full skill pipeline and one-shot persistence
---

# Single PDF Pipeline Agent

## Trigger
Use when processing a new PDF via agent flow (not from frontend upload), and commit results in one batch at the end.

## Input
- Pipeline controls: `file_path`, `filename`, `api_key`, `models`, `continue_count`, switches for tags/metadata.
- Persistence controls: `base_url`, `agent_token`.

## Output
- Final persistence response + debug section with intermediate outputs.

## Steps
1. Run `pdf-ingest-skill`.
2. If fingerprint exists, short-circuit with existing conversation info (default behavior).
3. Run `session-bootstrap-skill`.
4. Run `translate-full-paper-skill`.
5. Run `extract-figures/extract-tables/extract-tags/refresh-metadata` in parallel.
6. Run `compose-pipeline-bundle-skill`.
7. Run `persist-pipeline-bundle-skill` once.

## Failure Handling
- Blocking failures stop pipeline (`ingest/bootstrap/translate/persist`).
- Non-blocking extraction failures append `errors[]` and continue.

## References
- Read `references/contracts.md` for orchestration input schema.

## Script
- Entry: `scripts/run.py`
