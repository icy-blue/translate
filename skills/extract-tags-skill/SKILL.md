---
name: extract-tags-skill
description: Extract abstract snippet from the first bot translation and classify paper tags via Poe, without DB writes.
metadata:
  short-description: Classify tags from title and abstract snippet
---

# Extract Tags Skill

## Trigger
Use after translation when tag extraction is enabled.

## Input
- `enabled` (optional, default `true`)
- `title` (required if enabled)
- `first_bot_message` (required if enabled)
- `tag_model` (optional)
- `api_key` (required if enabled)

## Output
- `tags[]`
- `abstract` (for debugging)

## Steps
1. If disabled, return empty tags.
2. Derive abstract snippet with `extract_abstract_for_tagging`.
3. Classify tags with `classify_paper_tags`.
4. Return tags.

## Failure Handling
- Missing title/abstract: return empty tags with warning.
- Poe call failure: `ok=false` + `error.code=classify_failed`.

## References
- Read `references/contracts.md` for schema.
