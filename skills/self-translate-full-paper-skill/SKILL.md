---
name: self-translate-full-paper-skill
description: Translate a paper unit by unit with the executing agent's native reasoning instead of Poe, while preserving the same structured output contract as translate-full-paper-skill for later pipeline compatibility.
metadata:
  short-description: Agent-native full-paper translation with translate-skill-compatible output
---

# Self Full Paper Translate

## Overview

Use this skill when the user wants full-paper modular translation without Poe and wants the result to stay compatible with the existing translation bundle contract.

This is an agent-native skill. The executing agent reads the local PDF, plans translation units, translates one unit at a time, and emits the same structured result shape as `translate-full-paper-skill`.

The bundled `scripts/run.py` is only a compatibility bridge. It does not translate. It validates an agent-produced JSON artifact and re-emits the old skill contract for later pipeline integration.

## Workflow

### 1. Load the translation protocol

Read these files before translating:

1. [references/contracts.md](references/contracts.md)
2. [references/protocol.md](references/protocol.md)
3. [references/examples.md](references/examples.md)

Treat them as:

- `contracts.md`: input and output contract
- `protocol.md`: exact planning and per-unit translation rules
- `examples.md`: canonical output examples and edge cases

### 2. Read the local PDF

Use the local `file_path` from the request.

Prefer local Python PDF tooling already used in this repo. The default v1 path is text extraction plus visible heading recovery from the PDF itself.

If the PDF text is too noisy or section boundaries cannot be recovered reliably, return canonical `unsupported` output instead of inventing a plan.

### 3. Build the translation plan

Mirror the backend planner behavior from `backend.platform.config.settings.initial_prompt`.

The plan must:

- put abstract and main-body units in `units`
- put appendices in `appendix_units`
- preserve order
- avoid duplicates
- avoid invented boundaries
- return `unsupported` if section structure is unreliable

### 4. Translate one unit at a time

Mirror the backend unit translation behavior from `backend.platform.config.settings.continue_prompt`.

For each translated unit:

- translate only the current unit
- preserve heading numbering and visible prefixes
- emit canonical `translation_status`
- produce one hidden user message and one visible bot message
- keep `translation_plan` and `translation_status` in bot `client_payload`

If a later unit becomes ambiguous, stop there and return partial progress with canonical `UNSUPPORTED` state for that attempted unit.

### 5. Emit compatibility output

Return the same top-level fields as `translate-full-paper-skill`:

- `messages`
- `first_bot_message`
- `continue_count_used`
- `translation_plan`
- `translation_status`
- `errors`

Visible bot `content` must not include raw translation status markers.

## Output Rules

- Default to Simplified Chinese for translated content.
- Keep visible paper headings, numbering, formulas, citations, symbols, and references faithful to the PDF.
- Do not invent missing sections, appendix names, or subsection boundaries.
- If the structure is unreliable, prefer canonical `unsupported` output over guesswork.

## Bridge Runner

Use `scripts/run.py` only when you already have an agent-produced JSON artifact and need to validate or adapt it into the old `translate-full-paper-skill` contract.

The bridge input expects `agent_output_json`, which points to that artifact on disk.

## Built-in Resources

- [references/contracts.md](references/contracts.md): v1 input and output schema
- [references/protocol.md](references/protocol.md): planner and translator protocol
- [references/examples.md](references/examples.md): canonical example artifacts
- [scripts/run.py](scripts/run.py): compatibility bridge for agent-produced artifacts
