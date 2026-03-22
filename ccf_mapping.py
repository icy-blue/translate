from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
CCF_PUBLICATIONS_PATH = ROOT_DIR / "data" / "ccf_publications.json"


def _normalize_name(value: str) -> str:
    value = value.lower().strip()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\b(the|of|on|for|annual|international|ieee|acm|cvf)\b", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


@lru_cache(maxsize=1)
def _load_publications() -> list[dict[str, str]]:
    return json.loads(CCF_PUBLICATIONS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _build_exact_index() -> dict[str, dict[str, str]]:
    index: dict[str, dict[str, str]] = {}
    for item in _load_publications():
        for raw_name in {item["abbr"], item["full_name"]}:
            normalized = _normalize_name(raw_name)
            if normalized and normalized not in index:
                index[normalized] = item
    return index


def _containment_score(candidate: str, target: str) -> float:
    shorter = min(len(candidate), len(target))
    longer = max(len(candidate), len(target))
    if longer == 0:
        return 0.0
    return shorter / longer


def _find_containment_match(candidate: str) -> dict[str, str] | None:
    best_item: dict[str, str] | None = None
    best_score = 0.0
    ambiguous = False

    for item in _load_publications():
        names = {_normalize_name(item["abbr"]), _normalize_name(item["full_name"])}
        for target in names:
            if not target:
                continue
            if candidate in target or target in candidate:
                score = _containment_score(candidate, target)
                if score > best_score:
                    best_item = item
                    best_score = score
                    ambiguous = False
                elif score == best_score and best_item is not None and best_item != item:
                    ambiguous = True

    if ambiguous:
        return None
    return best_item


def map_ccf_publication(venue_names: list[str]) -> dict[str, str]:
    exact_index = _build_exact_index()

    normalized_candidates: list[str] = []
    for name in venue_names:
        normalized = _normalize_name(name or "")
        if normalized and normalized not in normalized_candidates:
            normalized_candidates.append(normalized)

    for candidate in normalized_candidates:
        item = exact_index.get(candidate)
        if item:
            return {
                "venue_abbr": item["abbr"],
                "ccf_category": item["category"],
                "ccf_type": item["type"],
            }

    for candidate in normalized_candidates:
        item = _find_containment_match(candidate)
        if item:
            return {
                "venue_abbr": item["abbr"],
                "ccf_category": item["category"],
                "ccf_type": item["type"],
            }

    return {
        "venue_abbr": "",
        "ccf_category": "None",
        "ccf_type": "None",
    }
