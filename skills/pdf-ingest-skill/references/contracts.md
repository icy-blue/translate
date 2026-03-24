# Contracts

## Input
```json
{
  "file_path": "/abs/path/paper.pdf",
  "file_bytes_base64": null,
  "filename": "paper.pdf",
  "check_existing": true
}
```

## Output
```json
{
  "ok": true,
  "filename": "paper.pdf",
  "file_size": 12345,
  "file_bytes_base64": "...",
  "fingerprint": "sha256hex",
  "is_existing": false,
  "existing_conversation_id": null,
  "errors": []
}
```
