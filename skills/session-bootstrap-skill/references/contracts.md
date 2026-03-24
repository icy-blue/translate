# Contracts

## Input
```json
{
  "filename": "paper.pdf",
  "file_bytes_base64": "...",
  "api_key": "poe_xxx",
  "title_model": "GPT-5.2-Instant"
}
```

## Output
```json
{
  "ok": true,
  "conversation_id": "abc123def456",
  "file_id": "uuidhex",
  "poe_attachment": {"url": "https://...", "content_type": "application/pdf", "name": "paper.pdf"},
  "title": "Paper Title",
  "file_record": {"filename": "paper.pdf", "poe_url": "https://...", "content_type": "application/pdf", "poe_name": "paper.pdf"},
  "errors": []
}
```
