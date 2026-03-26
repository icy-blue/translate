# Protocol

## Normative Sources

This skill mirrors the backend translation protocol from:

- `backend.platform.config.settings.initial_prompt`
- `backend.platform.config.settings.continue_prompt`
- `backend.domain.message_payloads`

Use those backend rules as the source of truth for planner semantics, unit translation semantics, and canonical status shaping.

## 1. Read the PDF

Use the local `file_path`.

Preferred v1 extraction strategy:

1. read the PDF with local Python tooling already present in the repo
2. extract page text conservatively
3. detect visible abstract, top-level sections, and appendix headings from the extracted text
4. treat extraction quality as a gating step, not a best-effort prompt invention step

If the extracted text does not support reliable section boundaries, stop and return canonical `unsupported`.

## 2. Build `translation_plan`

Planner requirements:

- abstract, if clearly present, must be first in `units`
- main-body sections belong in `units`
- appendices belong in `appendix_units`
- use top-level sections by default
- split only when a top-level section is clearly too long and a split follows existing subsection boundaries
- do not keep both a parent section and its child split units
- exclude title, authors, affiliations, keywords, acknowledgements, references, bibliography, and standalone figure or table captions
- preserve visible heading text with only whitespace normalization

If planning confidence is low, emit:

```json
{
  "status": "unsupported",
  "units": [],
  "appendix_units": [],
  "reason": "..."
}
```

## 3. Translate each unit

For the selected unit:

- translate only the current unit
- preserve numbering and visible heading prefixes exactly as shown
- use `#` for abstract and top-level section headings
- use `##` for second-level subsection headings when the current unit is a subsection split
- do not output synthetic `::` labels
- keep formulas, citations, symbols, and references intact whenever possible
- exclude standalone figure and table captions unless they are clearly part of the running prose for the current unit

If the unit cannot be located or bounded reliably, emit canonical unit failure:

```json
{
  "current_unit_id": "...",
  "state": "UNSUPPORTED",
  "reason": "..."
}
```

## 4. Maintain canonical status

After each translated or unsupported unit, maintain canonical `translation_status` compatible with `backend.domain.message_payloads.build_translation_status_payload`.

Required state progression:

- `IN_PROGRESS`: body units remain
- `BODY_DONE`: body is complete and appendix units remain
- `ALL_DONE`: all units are complete
- `UNSUPPORTED`: planner failed or the attempted unit was unreliable

## 5. Emit messages

The emitted `messages` list must remain backend-compatible:

1. hidden user trigger for the first translated step with `message_kind="system_prompt"`
2. visible bot reply for that step with canonical `client_payload`
3. zero or more hidden continuation user triggers with `message_kind="continue_command"`
4. visible bot replies for those continuation steps

For each bot reply:

- visible `content` must be the cleaned translation body only
- `client_payload.translation_plan` must be canonical
- `client_payload.translation_status` must be canonical

## 6. Partial success policy

If a later unit becomes ambiguous:

- keep all earlier successful messages
- emit the attempted unit as canonical `UNSUPPORTED`
- stop translating further units
- return the partial message list and latest canonical status

Do not continue past an ambiguous unit in v1.

## 7. Prefer fixed builder input over ad hoc Python

When you need to materialize a `.self_translate.json` artifact, prefer writing a compact JSON input for `scripts/run.py` with:

- `mode="build_artifact"`
- canonical `translation_plan`
- ordered `unit_results`
- optional `errors`

Do not generate a one-off Python script just to assemble the final artifact when the fixed builder can do it.

## 8. Prefer fixed Markdown rendering over ad hoc formatting

When you need a human-readable reading artifact for the user, prefer:

```bash
python skills/self-translate-full-paper-skill/scripts/render_markdown.py --input <artifact.json> --output <artifact.md>
```

Do not manually reformat the entire translation into a separate Markdown file when the standard renderer is sufficient.
