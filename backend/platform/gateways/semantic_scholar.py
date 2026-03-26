from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError

from sqlmodel import Session, select

from ...domain.ccf_mapping import map_ccf_publication
from ..config import settings
from ..models import PaperSemanticScholarResult

SEMANTIC_SCHOLAR_MATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
SEMANTIC_SCHOLAR_FIELDS = ",".join(
    [
        "paperId",
        "corpusId",
        "title",
        "year",
        "authors",
        "venue",
        "publicationVenue",
        "externalIds",
        "url",
        "abstract",
        "referenceCount",
        "citationCount",
        "influentialCitationCount",
        "publicationTypes",
        "publicationDate",
        "journal",
        "isOpenAccess",
        "openAccessPdf",
    ]
)


def resolve_semantic_scholar_api_key(api_key: str | None = None) -> str | None:
    return api_key or settings.semantic_scholar_api_key or settings.s2_api_key


def get_existing_result(session: Session, conversation_id: str) -> PaperSemanticScholarResult | None:
    statement = select(PaperSemanticScholarResult).where(PaperSemanticScholarResult.conversation_id == conversation_id)
    return session.exec(statement).first()


def build_request(title: str, api_key: str | None) -> urllib.request.Request:
    query_string = urllib.parse.urlencode({"query": title, "fields": SEMANTIC_SCHOLAR_FIELDS})
    headers = {"Accept": "application/json", "User-Agent": "translate-semantic-scholar/1.0"}
    resolved_api_key = resolve_semantic_scholar_api_key(api_key)
    if resolved_api_key:
        headers["x-api-key"] = resolved_api_key
    return urllib.request.Request(f"{SEMANTIC_SCHOLAR_MATCH_URL}?{query_string}", headers=headers)


def dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def get_retry_delay(exc: HTTPError | URLError, attempt: int) -> float:
    if isinstance(exc, HTTPError):
        retry_after = exc.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
    return min(30.0, 2**attempt)


def fetch_semantic_scholar_match(title: str, api_key: str | None = None, timeout: float = 30.0, max_retries: int = 4) -> dict[str, Any]:
    request = build_request(title, api_key)
    for attempt in range(max(0, max_retries) + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            should_retry = exc.code in {429, 500, 502, 503, 504} and attempt < max_retries
            if should_retry:
                time.sleep(get_retry_delay(exc, attempt))
                continue
            raise RuntimeError(f"HTTP {exc.code}: {body or exc.reason}") from exc
        except URLError as exc:
            if attempt < max_retries:
                time.sleep(get_retry_delay(exc, attempt))
                continue
            raise RuntimeError(f"Network error: {exc.reason}") from exc
    raise RuntimeError("Unexpected retry loop exit.")


def build_result_payload(conversation_id: str, response_payload: dict[str, Any]) -> dict[str, Any]:
    items = response_payload.get("data")
    if not isinstance(items, list):
        raise RuntimeError(f"Unexpected response payload: {response_payload!r}")

    matched_paper = items[0] if items else None
    ccf_mapping = {"venue_abbr": "", "ccf_category": "None", "ccf_type": "None"}
    if matched_paper:
        publication_venue = matched_paper.get("publicationVenue") or {}
        journal = matched_paper.get("journal") or {}
        venue_candidates = [matched_paper.get("venue"), publication_venue.get("name"), journal.get("name")]
        venue_candidates.extend(publication_venue.get("alternate_names") or [])
        ccf_mapping = map_ccf_publication([name for name in venue_candidates if isinstance(name, str)])

    payload: dict[str, Any] = {
        "conversation_id": conversation_id,
        "status": "matched" if matched_paper else "not_found",
        "paper_id": None,
        "corpus_id": None,
        "matched_title": None,
        "url": None,
        "abstract": None,
        "year": None,
        "venue": None,
        "venue_abbr": ccf_mapping["venue_abbr"],
        "ccf_category": ccf_mapping["ccf_category"],
        "ccf_type": ccf_mapping["ccf_type"],
        "publication_date": None,
        "is_open_access": None,
        "match_score": None,
        "citation_count": None,
        "reference_count": None,
        "authors_json": None,
        "external_ids_json": None,
        "publication_types_json": None,
        "publication_venue_json": None,
        "journal_json": None,
        "open_access_pdf_json": None,
        "raw_response_json": dump_json(response_payload) or "{}",
        "source": "semantic_scholar",
    }

    if matched_paper:
        payload.update(
            {
                "paper_id": matched_paper.get("paperId"),
                "corpus_id": matched_paper.get("corpusId"),
                "matched_title": matched_paper.get("title"),
                "url": matched_paper.get("url"),
                "abstract": matched_paper.get("abstract"),
                "year": matched_paper.get("year"),
                "venue": matched_paper.get("venue"),
                "publication_date": matched_paper.get("publicationDate"),
                "is_open_access": matched_paper.get("isOpenAccess"),
                "match_score": matched_paper.get("matchScore"),
                "citation_count": matched_paper.get("citationCount"),
                "reference_count": matched_paper.get("referenceCount"),
                "authors_json": dump_json(matched_paper.get("authors")),
                "external_ids_json": dump_json(matched_paper.get("externalIds")),
                "publication_types_json": dump_json(matched_paper.get("publicationTypes")),
                "publication_venue_json": dump_json(matched_paper.get("publicationVenue")),
                "journal_json": dump_json(matched_paper.get("journal")),
                "open_access_pdf_json": dump_json(matched_paper.get("openAccessPdf")),
            }
        )

    return payload


def upsert_result(session: Session, payload: dict[str, Any]) -> PaperSemanticScholarResult:
    existing = get_existing_result(session, payload["conversation_id"])
    now = datetime.now(timezone.utc)
    if existing is None:
        result = PaperSemanticScholarResult(**payload, created_at=now, updated_at=now)
        session.add(result)
    else:
        result = existing
        for key, value in payload.items():
            setattr(result, key, value)
        result.updated_at = now
    session.commit()
    session.refresh(result)
    return result


def refresh_semantic_scholar_result(
    session: Session,
    conversation_id: str,
    title: str,
    api_key: str | None = None,
    timeout: float = 30.0,
    max_retries: int = 4,
) -> PaperSemanticScholarResult:
    normalized_title = (title or "").strip()
    if not normalized_title:
        raise RuntimeError("Missing title for Semantic Scholar lookup.")
    response_payload = fetch_semantic_scholar_match(title=normalized_title, api_key=api_key, timeout=timeout, max_retries=max_retries)
    payload = build_result_payload(conversation_id, response_payload)
    return upsert_result(session, payload)


def safe_refresh_semantic_scholar_result(
    session: Session,
    conversation_id: str,
    title: str,
    api_key: str | None = None,
    timeout: float = 30.0,
    max_retries: int = 4,
) -> PaperSemanticScholarResult | None:
    try:
        return refresh_semantic_scholar_result(
            session=session,
            conversation_id=conversation_id,
            title=title,
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
    except Exception as exc:
        print(f"Error refreshing Semantic Scholar result for conversation {conversation_id}: {exc}")
        session.rollback()
        return get_existing_result(session, conversation_id)
