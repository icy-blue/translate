# Contracts

Top-level bundle fields:
- `conversation_id?`
- `title`
- `file_record`
  - required: `filename`, `fingerprint`, `poe_url`
  - optional defaults: `content_type="application/pdf"`, `poe_name=filename`
- `messages`
- `figures`
- `tables`
- `tags`
- `meta?`
- `errors`

Persistence target: `POST /agent/pipeline/commits`
