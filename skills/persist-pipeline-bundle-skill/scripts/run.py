#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}, "response": None, "errors": []}


def _build_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/agent/pipeline/commits"


def main() -> int:
    parser = argparse.ArgumentParser(description="persist-pipeline-bundle-skill runner")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    try:
        payload = _read_json(args.input_json)
    except Exception as exc:
        _write_json(args.output_json, _result_error("invalid_input", f"Failed to parse input json: {exc}"))
        return 1

    base_url = str(payload.get("base_url", "")).strip().rstrip("/")
    agent_token = str(payload.get("agent_token", "")).strip()
    bundle = payload.get("bundle") if isinstance(payload.get("bundle"), dict) else None

    if not base_url:
        _write_json(args.output_json, _result_error("invalid_input", "base_url is required."))
        return 1
    if not agent_token:
        _write_json(args.output_json, _result_error("invalid_input", "agent_token is required."))
        return 1
    if not bundle:
        _write_json(args.output_json, _result_error("invalid_input", "bundle is required."))
        return 1

    url = _build_endpoint(base_url)
    data = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "x-agent-token": agent_token,
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        _write_json(args.output_json, _result_error("persist_failed", f"HTTP {exc.code}: {body or exc.reason}"))
        return 1
    except Exception as exc:
        _write_json(args.output_json, _result_error("network_failed", str(exc)))
        return 1

    _write_json(args.output_json, {"ok": True, "response": parsed, "errors": []})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
