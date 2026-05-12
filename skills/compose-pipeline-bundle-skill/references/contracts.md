# Contracts

Top-level bundle fields:
- `conversation_id?`
- `title`
- `file_record`
  - required: `filename`, `fingerprint`, `poe_url`
  - optional defaults: `content_type="application/pdf"`, `poe_name=filename`
  - preferred `poe_url`: local `/files/{conversation_id}/{file_id}.pdf`
- `messages`
- `figures`
- `tables`
- `tags`
- `meta?`
- `errors`

Persistence target: `POST /agent/pipeline/commits`
