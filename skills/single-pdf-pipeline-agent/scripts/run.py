#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.platform.config import settings

SKILLS_ROOT = ROOT / "skills"


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _skill_script(skill_name: str) -> Path:
    return SKILLS_ROOT / skill_name / "scripts" / "run.py"


def _invoke_skill(skill_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    script = _skill_script(skill_name)
    if not script.exists():
        return {
            "ok": False,
            "error": {"code": "missing_skill", "message": f"Missing skill script: {script}"},
            "errors": [],
        }

    with tempfile.NamedTemporaryFile(suffix=".json") as in_fp, tempfile.NamedTemporaryFile(suffix=".json") as out_fp:
        Path(in_fp.name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        proc = subprocess.run(
            ["python", str(script), "--input-json", in_fp.name, "--output-json", out_fp.name],
            capture_output=True,
            text=True,
        )

        try:
            result = json.loads(Path(out_fp.name).read_text(encoding="utf-8"))
        except Exception:
            result = {
                "ok": False,
                "error": {
                    "code": "invalid_output",
                    "message": f"Skill {skill_name} did not write valid output json.",
                },
                "errors": [],
            }

    if proc.returncode != 0 and result.get("ok", False):
        result = {
            "ok": False,
            "error": {
                "code": "skill_failed",
                "message": f"{skill_name} exited with code {proc.returncode}: {(proc.stderr or proc.stdout).strip()}",
            },
            "errors": [],
        }

    return result


def _as_error(skill: str, result: dict[str, Any], retryable: bool = False) -> dict[str, Any]:
    error_obj = result.get("error") if isinstance(result.get("error"), dict) else {}
    return {
        "skill": skill,
        "type": error_obj.get("code", "skill_error"),
        "message": error_obj.get("message", "skill failed"),
        "retryable": retryable,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="single-pdf-pipeline-agent runner")
    parser.add_argument("--input-json", required=True)
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    try:
        request = _read_json(args.input_json)
    except Exception as exc:
        _write_json(
            args.output_json,
            {"ok": False, "error": {"code": "invalid_input", "message": f"Failed to parse input json: {exc}"}},
        )
        return 1

    aggregated_errors: list[dict[str, Any]] = []

    ingest = _invoke_skill(
        "pdf-ingest-skill",
        {
            "file_path": request.get("file_path"),
            "file_bytes_base64": request.get("file_bytes_base64"),
            "filename": request.get("filename"),
            "check_existing": request.get("check_existing", True),
        },
    )
    if not ingest.get("ok"):
        _write_json(args.output_json, {"ok": False, "stage": "pdf-ingest-skill", "result": ingest})
        return 1

    if ingest.get("errors"):
        aggregated_errors.extend(ingest.get("errors", []))

    skip_if_existing = bool(request.get("skip_if_existing", True))
    if skip_if_existing and ingest.get("is_existing"):
        _write_json(
            args.output_json,
            {
                "ok": True,
                "status": "succeeded",
                "exists": True,
                "conversation_id": ingest.get("existing_conversation_id"),
                "committed": False,
                "errors": aggregated_errors,
                "debug": {"ingest": ingest},
            },
        )
        return 0

    api_key = str(request.get("api_key", "")).strip()
    if not api_key:
        _write_json(
            args.output_json,
            {"ok": False, "error": {"code": "invalid_input", "message": "api_key is required."}, "stage": "bootstrap"},
        )
        return 1

    bootstrap = _invoke_skill(
        "session-bootstrap-skill",
        {
            "filename": ingest.get("filename") or request.get("filename"),
            "file_bytes_base64": ingest.get("file_bytes_base64"),
            "api_key": api_key,
            "title_model": request.get("title_model") or settings.poe_model,
            "conversation_id": request.get("conversation_id"),
        },
    )
    if not bootstrap.get("ok"):
        _write_json(args.output_json, {"ok": False, "stage": "session-bootstrap-skill", "result": bootstrap})
        return 1
    aggregated_errors.extend(bootstrap.get("errors", []))

    translate = _invoke_skill(
        "translate-full-paper-skill",
        {
            "api_key": api_key,
            "poe_model": request.get("poe_model") or settings.poe_model,
            "initial_prompt": request.get("initial_prompt") or settings.initial_prompt,
            "continue_count": int(request.get("continue_count", 0) or 0),
            "poe_attachment": bootstrap.get("poe_attachment"),
        },
    )
    if not translate.get("ok"):
        _write_json(args.output_json, {"ok": False, "stage": "translate-full-paper-skill", "result": translate})
        return 1
    aggregated_errors.extend(translate.get("errors", []))

    concurrent_jobs: dict[str, tuple[str, dict[str, Any], bool]] = {
        "figures": (
            "extract-figures-skill",
            {
                "file_bytes_base64": ingest.get("file_bytes_base64"),
                "preferred_direction": request.get("figure_direction"),
            },
            False,
        ),
        "tables": (
            "extract-tables-skill",
            {
                "file_bytes_base64": ingest.get("file_bytes_base64"),
                "preferred_direction": request.get("table_direction"),
            },
            False,
        ),
    }

    if bool(request.get("extract_tags", True)):
        concurrent_jobs["tags"] = (
            "extract-tags-skill",
            {
                "enabled": True,
                "title": bootstrap.get("title"),
                "first_bot_message": translate.get("first_bot_message", ""),
                "tag_model": request.get("tag_model") or settings.poe_model,
                "api_key": api_key,
            },
            False,
        )

    if bool(request.get("refresh_metadata", False)):
        concurrent_jobs["meta"] = (
            "refresh-metadata-skill",
            {
                "title": bootstrap.get("title"),
                "conversation_id": bootstrap.get("conversation_id"),
                "semantic_api_key": request.get("semantic_api_key"),
            },
            False,
        )

    concurrent_results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(concurrent_jobs))) as pool:
        future_map = {
            pool.submit(_invoke_skill, skill_name, skill_payload): (label, skill_name, non_blocking)
            for label, (skill_name, skill_payload, non_blocking) in concurrent_jobs.items()
        }
        for future in as_completed(future_map):
            label, skill_name, non_blocking = future_map[future]
            result = future.result()
            concurrent_results[label] = result
            if not result.get("ok"):
                aggregated_errors.append(_as_error(skill_name, result, retryable=not non_blocking))
            else:
                aggregated_errors.extend(result.get("errors", []))

    figures = concurrent_results.get("figures", {}).get("figures", []) if concurrent_results.get("figures", {}).get("ok") else []
    tables = concurrent_results.get("tables", {}).get("tables", []) if concurrent_results.get("tables", {}).get("ok") else []
    tags = concurrent_results.get("tags", {}).get("tags", []) if concurrent_results.get("tags", {}).get("ok") else []
    meta = concurrent_results.get("meta", {}).get("meta") if concurrent_results.get("meta", {}).get("ok") else None

    compose = _invoke_skill(
        "compose-pipeline-bundle-skill",
        {
            "conversation_id": bootstrap.get("conversation_id"),
            "title": bootstrap.get("title"),
            "file_record": {
                "filename": bootstrap.get("file_record", {}).get("filename"),
                "fingerprint": ingest.get("fingerprint"),
                "poe_url": bootstrap.get("file_record", {}).get("poe_url"),
                "content_type": bootstrap.get("file_record", {}).get("content_type"),
                "poe_name": bootstrap.get("file_record", {}).get("poe_name"),
            },
            "messages": translate.get("messages", []),
            "figures": figures,
            "tables": tables,
            "tags": tags,
            "meta": meta,
            "errors": aggregated_errors,
        },
    )
    if not compose.get("ok"):
        _write_json(args.output_json, {"ok": False, "stage": "compose-pipeline-bundle-skill", "result": compose})
        return 1

    base_url = str(request.get("base_url", "")).strip()
    agent_token = str(request.get("agent_token", "")).strip()
    persist = _invoke_skill(
        "persist-pipeline-bundle-skill",
        {
            "base_url": base_url,
            "agent_token": agent_token,
            "bundle": compose.get("bundle"),
        },
    )
    if not persist.get("ok"):
        _write_json(args.output_json, {"ok": False, "stage": "persist-pipeline-bundle-skill", "result": persist})
        return 1

    _write_json(
        args.output_json,
        {
            "ok": True,
            "status": "succeeded",
            "response": persist.get("response"),
            "errors": aggregated_errors,
            "debug": {
                "ingest": ingest,
                "bootstrap": bootstrap,
                "translate": translate,
                "parallel": concurrent_results,
                "compose": compose,
            },
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
