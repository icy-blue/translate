from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import or_
from sqlmodel import Session, func, select

from ..persistence import crud
from ..persistence.models import Conversation, PaperSemanticScholarResult, PaperTag
from .serializers import build_conversations_data


def normalize_string_filters(values: Optional[list[str]]) -> list[str]:
    if not values:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value is None:
            continue
        candidate = value.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        normalized.append(candidate)
    return normalized


def normalize_year_filters(values: Optional[list[str]]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for value in values or []:
        try:
            year = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if year in seen:
            continue
        seen.add(year)
        normalized.append(year)
    return normalized


def normalize_tag_codes(tag_codes: Optional[list[str]]) -> list[str]:
    if not tag_codes:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for tag_code in tag_codes:
        if not tag_code:
            continue
        code = tag_code.strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        normalized.append(code)
    return normalized


def build_filtered_conversation_statement(
    tag_codes: Optional[list[str]] = None,
    ccf_categories: Optional[list[str]] = None,
    venue_filters: Optional[list[str]] = None,
    years: Optional[list[int]] = None,
):
    statement = select(Conversation)

    normalized_tag_codes = normalize_tag_codes(tag_codes)
    if normalized_tag_codes:
        tagged_conversation_ids = (
            select(PaperTag.conversation_id)
            .where(PaperTag.tag_code.in_(normalized_tag_codes))
            .group_by(PaperTag.conversation_id)
            .having(func.count(func.distinct(PaperTag.tag_code)) == len(normalized_tag_codes))
        )
        statement = statement.where(Conversation.id.in_(tagged_conversation_ids))

    normalized_ccf_categories = normalize_string_filters(ccf_categories)
    if normalized_ccf_categories:
        semantic_ids = select(PaperSemanticScholarResult.conversation_id)
        ccf_conditions = []
        real_categories = [value for value in normalized_ccf_categories if value in {"A", "B", "C"}]
        if real_categories:
            ccf_conditions.append(
                Conversation.id.in_(
                    select(PaperSemanticScholarResult.conversation_id).where(
                        PaperSemanticScholarResult.ccf_category.in_(real_categories)
                    )
                )
            )
        if "None" in normalized_ccf_categories:
            ccf_conditions.append(
                Conversation.id.in_(
                    select(PaperSemanticScholarResult.conversation_id).where(
                        PaperSemanticScholarResult.ccf_category == "None"
                    )
                )
            )
            ccf_conditions.append(~Conversation.id.in_(semantic_ids))
        if ccf_conditions:
            statement = statement.where(or_(*ccf_conditions))

    normalized_venues = normalize_string_filters(venue_filters)
    if normalized_venues:
        statement = statement.where(
            Conversation.id.in_(
                select(PaperSemanticScholarResult.conversation_id).where(
                    or_(
                        PaperSemanticScholarResult.venue_abbr.in_(normalized_venues),
                        PaperSemanticScholarResult.venue.in_(normalized_venues),
                    )
                )
            )
        )

    normalized_years = [year for year in (years or []) if isinstance(year, int)]
    if normalized_years:
        statement = statement.where(
            Conversation.id.in_(
                select(PaperSemanticScholarResult.conversation_id).where(
                    PaperSemanticScholarResult.year.in_(normalized_years)
                )
            )
        )

    return statement


def count_filtered_conversations(
    session: Session,
    tag_codes: Optional[list[str]] = None,
    ccf_categories: Optional[list[str]] = None,
    venue_filters: Optional[list[str]] = None,
    years: Optional[list[int]] = None,
) -> int:
    filtered_statement = build_filtered_conversation_statement(
        tag_codes=tag_codes,
        ccf_categories=ccf_categories,
        venue_filters=venue_filters,
        years=years,
    )
    count_statement = select(func.count()).select_from(filtered_statement.subquery())
    return session.exec(count_statement).one()


def build_search_filter_payload(session: Session) -> dict:
    total_conversations = session.exec(select(func.count(Conversation.id))).one()
    ccf_counts = {
        category: count
        for category, count in session.exec(
            select(
                PaperSemanticScholarResult.ccf_category,
                func.count(PaperSemanticScholarResult.conversation_id),
            ).group_by(PaperSemanticScholarResult.ccf_category)
        ).all()
    }
    ccf_known_count = sum(ccf_counts.get(category, 0) for category in ("A", "B", "C"))
    ccf_none_count = max(0, total_conversations - ccf_known_count)

    venue_rows = session.exec(
        select(
            PaperSemanticScholarResult.venue_abbr,
            PaperSemanticScholarResult.venue,
        ).where(
            or_(
                PaperSemanticScholarResult.venue_abbr != "",
                PaperSemanticScholarResult.venue.is_not(None),
            )
        )
    ).all()
    venue_counts: dict[str, dict] = {}
    for venue_abbr, venue in venue_rows:
        value = venue_abbr or venue
        if not value:
            continue
        entry = venue_counts.setdefault(
            value,
            {
                "value": value,
                "label": venue_abbr or venue,
                "full_label": venue or venue_abbr or value,
                "count": 0,
            },
        )
        entry["count"] += 1

    year_counts = session.exec(
        select(
            PaperSemanticScholarResult.year,
            func.count(PaperSemanticScholarResult.conversation_id),
        )
        .where(PaperSemanticScholarResult.year.is_not(None))
        .group_by(PaperSemanticScholarResult.year)
        .order_by(PaperSemanticScholarResult.year.desc())
    ).all()

    return {
        "total_conversations": total_conversations,
        "ccf_categories": [
            {"value": "A", "label": "CCF-A", "count": ccf_counts.get("A", 0)},
            {"value": "B", "label": "CCF-B", "count": ccf_counts.get("B", 0)},
            {"value": "C", "label": "CCF-C", "count": ccf_counts.get("C", 0)},
            {"value": "None", "label": "CCF-None", "count": ccf_none_count},
        ],
        "venues": sorted(venue_counts.values(), key=lambda item: item["label"].lower()),
        "years": [
            {"value": str(year), "label": str(year), "count": count}
            for year, count in year_counts
            if year is not None
        ],
    }


def get_tag_usage_counts(session: Session) -> dict[str, int]:
    statement = (
        select(PaperTag.tag_code, func.count(func.distinct(PaperTag.conversation_id)))
        .group_by(PaperTag.tag_code)
    )
    return {tag_code: count for tag_code, count in session.exec(statement).all()}


def calculate_relevance(title: str, query: str) -> int:
    if not title:
        return 0
    title_lower, query_lower = title.lower(), query.lower()
    if query_lower == title_lower:
        return 100
    if query_lower in title_lower:
        return 50
    return 0


def search_conversation_payload(
    session: Session,
    *,
    q: str = "",
    search_type: str = "all",
    tag_code: Optional[list[str]] = None,
    ccf_category: Optional[list[str]] = None,
    venue_filter: Optional[list[str]] = None,
    year: Optional[list[str]] = None,
) -> dict:
    normalized_tag_codes = normalize_tag_codes(tag_code)
    normalized_ccf_categories = normalize_string_filters(ccf_category)
    normalized_venue_filters = normalize_string_filters(venue_filter)
    normalized_years = normalize_year_filters(year)
    total_conversations = session.exec(select(func.count(Conversation.id))).one()
    if not (q and q.strip()) and not normalized_tag_codes and not normalized_ccf_categories and not normalized_venue_filters and not normalized_years:
        return {"exact_matches": [], "fuzzy_matches": [], "total_conversations": total_conversations}

    query = q.strip()
    base_statement = build_filtered_conversation_statement(
        tag_codes=normalized_tag_codes,
        ccf_categories=normalized_ccf_categories,
        venue_filters=normalized_venue_filters,
        years=normalized_years,
    )

    if query:
        exact_statement = base_statement.where(Conversation.title.ilike(f"%{query}%")).order_by(Conversation.created_at.desc()).limit(5)
        exact_convs = session.exec(exact_statement).all()
        exact_relevance_scores = [calculate_relevance(c.title or "", query) for c in exact_convs]
    else:
        exact_statement = base_statement.order_by(Conversation.created_at.desc()).limit(10)
        exact_convs = session.exec(exact_statement).all()
        exact_relevance_scores = [100] * len(exact_convs)
    exact_matches = build_conversations_data(session, exact_convs, True, exact_relevance_scores)

    fuzzy_matches = []
    query_words = [word.lower() for word in query.split() if len(word) > 1]
    if query_words:
        all_fuzzy_statement = base_statement.where(~Conversation.title.ilike(f"%{query}%")).order_by(Conversation.created_at.desc())
        all_fuzzy = session.exec(all_fuzzy_statement).all()
        fuzzy_candidates = []
        for conversation in all_fuzzy:
            title = (conversation.title or "").lower()
            relevance = sum(
                len(word) + 5 if re.search(r"\b" + re.escape(word) + r"\b", title) else len(word)
                for word in query_words
                if word in title
            )
            if relevance > 0:
                fuzzy_candidates.append((conversation, relevance))

        fuzzy_candidates.sort(key=lambda item: (-item[1], item[0].created_at))
        fuzzy_convs = [conversation for conversation, _ in fuzzy_candidates[:5]]
        fuzzy_relevance_scores = [relevance for _, relevance in fuzzy_candidates[:5]]
        fuzzy_matches = build_conversations_data(session, fuzzy_convs, True, fuzzy_relevance_scores)

    return {
        "exact_matches": exact_matches if search_type != "fuzzy" else [],
        "fuzzy_matches": fuzzy_matches if search_type != "exact" else [],
        "total_conversations": total_conversations,
    }
