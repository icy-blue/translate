from __future__ import annotations

import re

import fitz
from PIL import Image

FIGURE_CAPTION_PATTERN = re.compile(
    r"^\s*((?:fig(?:ure)?\.?\s*(?:\d+|[ivxlcdm]+)[a-z]?)|(?:图\s*\d+[a-z]?))\b",
    re.IGNORECASE,
)
FIGURE_CAPTION_SEARCH_PATTERN = re.compile(
    r"((?:fig(?:ure)?\.?\s*(?:\d+|[ivxlcdm]+)[a-z]?)|(?:图\s*\d+[a-z]?))\s*[:.]?",
    re.IGNORECASE,
)
TABLE_CAPTION_PATTERN = re.compile(
    r"^\s*((?:table\.?\s*(?:\d+|[ivxlcdm]+)[a-z]?))\b",
    re.IGNORECASE,
)
TABLE_CAPTION_SEARCH_PATTERN = re.compile(
    r"((?:table\.?\s*(?:\d+|[ivxlcdm]+)[a-z]?))\s*[:.]?",
    re.IGNORECASE,
)
MAX_CAPTION_GAP = 260
MAX_TABLE_GAP = 260
MIN_IMAGE_WIDTH = 40
MIN_IMAGE_HEIGHT = 16
MIN_DRAWING_WIDTH = 40
MIN_DRAWING_HEIGHT = 16
MIN_TABLE_TEXT_WIDTH = 24
MIN_TABLE_TEXT_HEIGHT = 8
RENDER_SCALE = 2.0
CONTEXT_TEXT_GAP = 14
CLIP_PADDING = 8
REGION_GROUP_GAP = 40
TABLE_REGION_GROUP_GAP = 56
TABLE_COLUMN_PADDING = 24
SINGLE_COLUMN_TABLE_RATIO = 0.55


def extract_pdf_figures(file_bytes: bytes, preferred_direction: str | None = None) -> list[dict]:
    """Extract figure images with captions from a PDF and encode them as WebP."""
    document = fitz.open(stream=file_bytes, filetype="pdf")
    extracted_figures: list[dict] = []
    figure_index = 1
    preferred_direction = preferred_direction or _infer_figure_preferred_direction(document)

    try:
        for page_number in range(document.page_count):
            page = document[page_number]
            blocks = page.get_text("dict").get("blocks", [])
            graphic_regions = _collect_graphic_regions(page, blocks)
            caption_blocks = _collect_caption_blocks(blocks, FIGURE_CAPTION_PATTERN)
            text_regions = _collect_non_caption_text_regions(blocks)
            used_region_indexes: set[int] = set()

            for caption in caption_blocks:
                matched_indexes = _match_graphic_region_indexes(
                    caption,
                    graphic_regions,
                    used_region_indexes,
                    preferred_direction=preferred_direction,
                )
                if not matched_indexes:
                    continue

                figure_rect = _union_rects([graphic_regions[index]["bbox"] for index in matched_indexes])
                figure_rect = _expand_with_context_text(figure_rect, text_regions, caption["bbox"], page.rect)
                figure_rect = _constrain_to_caption_column(figure_rect, caption["bbox"], page.rect)
                image_data, image_width, image_height = _render_rect_as_webp(page, figure_rect)

                extracted_figures.append({
                    "page_number": page_number + 1,
                    "figure_index": figure_index,
                    "figure_label": caption["label"],
                    "caption": caption["text"],
                    "image_mime_type": "image/webp",
                    "image_data": image_data,
                    "image_width": image_width,
                    "image_height": image_height,
                })
                used_region_indexes.update(matched_indexes)
                figure_index += 1
    finally:
        document.close()

    return extracted_figures


def extract_pdf_tables(file_bytes: bytes, preferred_direction: str | None = None) -> list[dict]:
    """Extract table snapshots with captions from a PDF and encode them as WebP."""
    document = fitz.open(stream=file_bytes, filetype="pdf")
    extracted_tables: list[dict] = []
    table_index = 1
    preferred_direction = preferred_direction or _infer_table_preferred_direction(document)

    try:
        for page_number in range(document.page_count):
            page = document[page_number]
            blocks = page.get_text("dict").get("blocks", [])
            caption_blocks = _collect_table_caption_blocks(blocks)
            if not caption_blocks:
                continue

            table_regions = _collect_table_regions(page, blocks)
            used_region_indexes: set[int] = set()

            for caption in caption_blocks:
                matched_indexes = _match_table_region_indexes(
                    caption,
                    table_regions,
                    used_region_indexes,
                    preferred_direction=preferred_direction,
                )
                if not matched_indexes:
                    continue

                table_rect = _union_rects([table_regions[index]["bbox"] for index in matched_indexes])
                table_rect = _constrain_table_to_caption_column(table_rect, caption["bbox"], page.rect)
                table_rect = _pad_rect(table_rect, page.rect)
                image_data, image_width, image_height = _render_rect_as_webp(page, table_rect)

                extracted_tables.append({
                    "page_number": page_number + 1,
                    "table_index": table_index,
                    "table_label": caption["label"],
                    "caption": caption["text"],
                    "image_mime_type": "image/webp",
                    "image_data": image_data,
                    "image_width": image_width,
                    "image_height": image_height,
                })
                used_region_indexes.update(matched_indexes)
                table_index += 1
    finally:
        document.close()

    return extracted_tables


def _collect_graphic_regions(page: fitz.Page, blocks: list[dict]) -> list[dict]:
    regions = []

    for block in blocks:
        if block.get("type") != 1:
            continue
        bbox = fitz.Rect(block["bbox"])
        if bbox.width < MIN_IMAGE_WIDTH or bbox.height < MIN_IMAGE_HEIGHT:
            continue
        regions.append({"bbox": bbox, "kind": "image"})

    for bbox in page.cluster_drawings():
        rect = fitz.Rect(bbox)
        if rect.width < MIN_DRAWING_WIDTH or rect.height < MIN_DRAWING_HEIGHT:
            continue
        regions.append({"bbox": rect, "kind": "drawing"})

    regions.sort(key=lambda block: (block["bbox"].y0, block["bbox"].x0))
    return _dedupe_regions(regions)


def _collect_caption_blocks(blocks: list[dict], pattern: re.Pattern[str]) -> list[dict]:
    caption_blocks = []
    for block in blocks:
        if block.get("type") != 0:
            continue

        text = _normalize_whitespace(_extract_block_text(block))
        if not text:
            continue

        match = _find_caption_match(text, pattern)
        if not match:
            continue

        caption_blocks.append({
            "bbox": fitz.Rect(block["bbox"]),
            "label": match.group(1).strip(),
            "text": text,
        })

    caption_blocks.sort(key=lambda block: (block["bbox"].y0, block["bbox"].x0))
    return caption_blocks


def _collect_table_caption_blocks(blocks: list[dict]) -> list[dict]:
    caption_blocks = []

    for block in blocks:
        if block.get("type") != 0:
            continue

        text = _normalize_whitespace(_extract_block_text(block))
        if not text:
            continue
        match = TABLE_CAPTION_PATTERN.match(text)
        if not match:
            continue
        if not _has_caption_style_suffix(text, match.end()):
            continue

        caption_blocks.append({
            "bbox": fitz.Rect(block["bbox"]),
            "label": match.group(1).strip(),
            "text": text,
        })

    for block in blocks:
        if block.get("type") != 0:
            continue

        caption = _extract_table_caption_from_block(block)
        if not caption:
            continue

        if any(_is_nearly_same_rect(caption["bbox"], existing["bbox"]) for existing in caption_blocks):
            continue
        caption_blocks.append(caption)

    caption_blocks.sort(key=lambda block: (block["bbox"].y0, block["bbox"].x0))
    return _extend_table_captions(caption_blocks, blocks)


def _find_caption_match(text: str, pattern: re.Pattern[str]):
    match = pattern.match(text)
    if match:
        return match

    search_pattern = FIGURE_CAPTION_SEARCH_PATTERN if pattern is FIGURE_CAPTION_PATTERN else TABLE_CAPTION_SEARCH_PATTERN
    inline_match = search_pattern.search(text)
    if not inline_match:
        return None

    if inline_match.start() > 48:
        return None

    prefix = text[:inline_match.start()].strip()
    if len(prefix) > 24:
        return None
    if any(char in prefix for char in ".:;!?"):
        return None

    return inline_match


def _extract_table_caption_from_block(block: dict) -> dict | None:
    lines = _get_text_lines(block)
    if not lines:
        return None

    start_index = None
    label = None
    for index, line in enumerate(lines):
        match = TABLE_CAPTION_PATTERN.match(line["text"])
        if not match:
            continue
        if not _has_caption_style_suffix(line["text"], match.end()):
            continue
        start_index = index
        label = match.group(1).strip()
        break

    if start_index is None or label is None:
        return None

    caption_lines = lines[start_index:]
    caption_rect = _union_rects([fitz.Rect(line["bbox"]) for line in caption_lines])
    caption_text = _normalize_whitespace(" ".join(line["text"] for line in caption_lines))
    if not caption_text:
        return None

    return {
        "bbox": caption_rect,
        "label": label,
        "text": caption_text,
    }


def _collect_non_caption_text_regions(blocks: list[dict]) -> list[fitz.Rect]:
    regions: list[fitz.Rect] = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        text = _normalize_whitespace(_extract_block_text(block))
        if not text:
            continue
        if FIGURE_CAPTION_PATTERN.match(text) or TABLE_CAPTION_PATTERN.match(text):
            continue
        regions.append(fitz.Rect(block["bbox"]))
    return regions


def _collect_table_regions(page: fitz.Page, blocks: list[dict]) -> list[dict]:
    regions: list[dict] = []
    top_margin_limit = page.rect.y0 + 28
    bottom_margin_limit = page.rect.y1 - 28

    for block in blocks:
        if block.get("type") != 0:
            continue
        block_lines = _get_text_lines(block)
        if not block_lines:
            continue

        caption_start_index = _find_table_caption_line_index(block_lines)
        content_lines = block_lines if caption_start_index is None else block_lines[:caption_start_index]
        if not content_lines:
            continue

        text = _normalize_whitespace(" ".join(line["text"] for line in content_lines))
        if not text:
            continue
        if FIGURE_CAPTION_PATTERN.match(text) or TABLE_CAPTION_PATTERN.match(text):
            continue

        bbox = _union_rects([fitz.Rect(line["bbox"]) for line in content_lines])
        if bbox.width < MIN_TABLE_TEXT_WIDTH or bbox.height < MIN_TABLE_TEXT_HEIGHT:
            continue

        line_count = len(content_lines)
        if line_count <= 2 and (bbox.y1 <= top_margin_limit or bbox.y0 >= bottom_margin_limit):
            continue

        if line_count <= 2 and bbox.width < 120 and bbox.height < 20 and _digit_ratio(text) < 0.2:
            continue

        if _is_line_number_polluted_paragraph(content_lines):
            continue

        digit_ratio = _digit_ratio(text)
        if _is_paragraph_like(text, line_count) and digit_ratio < 0.05:
            continue

        regions.append({
            "bbox": bbox,
            "kind": "text",
            "text": text,
            "line_count": line_count,
        })

    for bbox in page.cluster_drawings():
        rect = fitz.Rect(bbox)
        if rect.width < MIN_DRAWING_WIDTH or rect.height < MIN_DRAWING_HEIGHT:
            continue
        regions.append({"bbox": rect, "kind": "drawing", "text": "", "line_count": 0})

    regions.sort(key=lambda block: (block["bbox"].y0, block["bbox"].x0))
    return _dedupe_regions(regions)


def _get_text_lines(block: dict) -> list[dict]:
    lines: list[dict] = []
    for line in block.get("lines", []):
        line_text = "".join(span.get("text", "") for span in line.get("spans", []))
        line_text = _normalize_whitespace(line_text)
        if not line_text:
            continue
        lines.append({
            "text": line_text,
            "bbox": tuple(line["bbox"]),
        })
    return lines


def _find_table_caption_line_index(lines: list[dict]) -> int | None:
    for index, line in enumerate(lines):
        match = TABLE_CAPTION_PATTERN.match(line["text"])
        if match and _has_caption_style_suffix(line["text"], match.end()):
            return index
    return None


def _has_caption_style_suffix(text: str, match_end: int) -> bool:
    suffix = text[match_end:].lstrip()
    if not suffix:
        return True

    first_char = suffix[0]
    if first_char in ".:;,-(":
        return True
    if first_char.isupper() or first_char.isdigit():
        return True
    return False


def _is_line_number_polluted_paragraph(lines: list[dict]) -> bool:
    if len(lines) < 4:
        return False

    polluted_lines = 0
    numeric_only_lines = 0
    alpha_chars = 0
    for line in lines:
        text = line["text"]
        alpha_chars += sum(1 for char in text if char.isalpha())
        if re.search(r"\b\d{2,4}\s+\d{2,4}\s*$", text):
            polluted_lines += 1
        if re.fullmatch(r"\d{2,4}", text):
            numeric_only_lines += 1

    if polluted_lines >= max(3, len(lines) // 2) and alpha_chars > 40:
        return True
    return numeric_only_lines >= max(3, len(lines) // 3) and alpha_chars > 40


def _extract_block_text(block: dict) -> str:
    lines: list[str] = []
    for line in block.get("lines", []):
        line_text = "".join(span.get("text", "") for span in line.get("spans", []))
        if line_text.strip():
            lines.append(line_text.strip())
    return " ".join(lines)


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _match_graphic_region_indexes(
    caption: dict,
    regions: list[dict],
    used_region_indexes: set[int],
    preferred_direction: str | None = None,
) -> list[int]:
    above_matches = _match_graphic_region_indexes_in_direction(caption, regions, used_region_indexes, "above")
    below_matches = _match_graphic_region_indexes_in_direction(caption, regions, used_region_indexes, "below")

    if preferred_direction == "above" and above_matches:
        return above_matches
    if preferred_direction == "below" and below_matches:
        return below_matches

    if not above_matches and not below_matches:
        return []
    if above_matches and not below_matches:
        return above_matches
    if below_matches and not above_matches:
        return below_matches

    above_score = _score_graphic_group(above_matches, regions)
    below_score = _score_graphic_group(below_matches, regions)

    if above_score >= below_score - 0.5:
        return above_matches
    return below_matches


def _match_graphic_region_indexes_in_direction(
    caption: dict,
    regions: list[dict],
    used_region_indexes: set[int],
    direction: str,
) -> list[int]:
    caption_bbox = caption["bbox"]
    candidates: list[tuple[int, fitz.Rect, float]] = []

    for index, region in enumerate(regions):
        if index in used_region_indexes:
            continue

        region_bbox = region["bbox"]
        if direction == "above":
            vertical_gap = caption_bbox.y0 - region_bbox.y1
        else:
            vertical_gap = region_bbox.y0 - caption_bbox.y1
        if vertical_gap < -6 or vertical_gap > MAX_CAPTION_GAP:
            continue

        if not _has_horizontal_match(region_bbox, caption_bbox):
            continue

        candidates.append((index, region_bbox, vertical_gap))

    if not candidates:
        return []

    best_indexes: list[int] = []
    best_score: float | None = None

    for anchor_index, anchor_bbox, anchor_gap in candidates:
        matched_indexes = {anchor_index}
        group_rect = fitz.Rect(anchor_bbox)
        changed = True

        while changed:
            changed = False
            for index, region_bbox, _ in candidates:
                if index in matched_indexes:
                    continue
                if _is_region_adjacent(group_rect, region_bbox):
                    matched_indexes.add(index)
                    group_rect |= region_bbox
                    changed = True

        indexes = sorted(matched_indexes)
        score = _score_graphic_group(indexes, regions) - (anchor_gap * 0.02)
        if best_score is None or score > best_score:
            best_indexes = indexes
            best_score = score

    return best_indexes


def _match_table_region_indexes(
    caption: dict,
    regions: list[dict],
    used_region_indexes: set[int],
    preferred_direction: str | None = None,
) -> list[int]:
    above_matches = _match_table_region_indexes_in_direction(caption, regions, used_region_indexes, "above")
    below_matches = _match_table_region_indexes_in_direction(caption, regions, used_region_indexes, "below")

    if preferred_direction == "above" and above_matches:
        return above_matches
    if preferred_direction == "below" and below_matches:
        return below_matches

    if not above_matches and not below_matches:
        return []
    if above_matches and not below_matches:
        return above_matches
    if below_matches and not above_matches:
        return below_matches

    above_score = _score_table_group(above_matches, regions)
    below_score = _score_table_group(below_matches, regions)

    if above_score >= below_score - 0.75:
        return above_matches
    return below_matches


def _infer_table_preferred_direction(document: fitz.Document) -> str | None:
    above_votes = 0
    below_votes = 0

    for page_number in range(document.page_count):
        page = document[page_number]
        blocks = page.get_text("dict").get("blocks", [])
        caption_blocks = _collect_table_caption_blocks(blocks)
        if not caption_blocks:
            continue

        table_regions = _collect_table_regions(page, blocks)
        used_region_indexes: set[int] = set()

        for caption in caption_blocks:
            above_matches = _match_table_region_indexes_in_direction(caption, table_regions, used_region_indexes, "above")
            below_matches = _match_table_region_indexes_in_direction(caption, table_regions, used_region_indexes, "below")

            if above_matches and not below_matches:
                above_votes += 1
            elif below_matches and not above_matches:
                below_votes += 1

            matched_indexes = _match_table_region_indexes(caption, table_regions, used_region_indexes)
            used_region_indexes.update(matched_indexes)

    if above_votes == 0 and below_votes == 0:
        return None
    if above_votes == below_votes:
        return None
    if above_votes > below_votes:
        return "above"
    return "below"


def _infer_figure_preferred_direction(document: fitz.Document) -> str | None:
    above_votes = 0
    below_votes = 0

    for page_number in range(document.page_count):
        page = document[page_number]
        blocks = page.get_text("dict").get("blocks", [])
        caption_blocks = _collect_caption_blocks(blocks, FIGURE_CAPTION_PATTERN)
        if not caption_blocks:
            continue

        graphic_regions = _collect_graphic_regions(page, blocks)
        for caption in caption_blocks:
            above_matches = _match_graphic_region_indexes_in_direction(caption, graphic_regions, set(), "above")
            below_matches = _match_graphic_region_indexes_in_direction(caption, graphic_regions, set(), "below")

            if above_matches and not below_matches:
                above_votes += 1
                continue
            if below_matches and not above_matches:
                below_votes += 1
                continue

            if not above_matches or not below_matches:
                continue

            above_score = _score_graphic_group(above_matches, graphic_regions)
            below_score = _score_graphic_group(below_matches, graphic_regions)
            if above_score >= below_score + 0.75:
                above_votes += 1
            elif below_score >= above_score + 0.75:
                below_votes += 1

    if above_votes == 0 and below_votes == 0:
        return None
    if above_votes == below_votes:
        return None
    if above_votes > below_votes:
        return "above"
    return "below"


def _score_graphic_group(indexes: list[int], regions: list[dict]) -> float:
    score = 0.0
    area = 0.0

    for index in indexes:
        bbox = regions[index]["bbox"]
        area += bbox.width * bbox.height
        if regions[index]["kind"] == "image":
            score += 2.0
        else:
            score += 1.4
        if bbox.width >= 140:
            score += 0.5
        if bbox.height >= 60:
            score += 0.5

    if len(indexes) >= 2:
        score += 0.8
    score += min(area / 12000.0, 6.0)
    return score


def _match_table_region_indexes_in_direction(
    caption: dict,
    regions: list[dict],
    used_region_indexes: set[int],
    direction: str,
) -> list[int]:
    caption_bbox = caption["bbox"]
    candidates: list[tuple[int, fitz.Rect, float]] = []

    for index, region in enumerate(regions):
        if index in used_region_indexes:
            continue

        region_bbox = region["bbox"]
        if not _has_horizontal_match(region_bbox, caption_bbox):
            continue

        if direction == "above":
            vertical_gap = caption_bbox.y0 - region_bbox.y1
        else:
            vertical_gap = region_bbox.y0 - caption_bbox.y1

        if vertical_gap < -6 or vertical_gap > MAX_TABLE_GAP:
            continue

        candidates.append((index, region_bbox, vertical_gap))

    if not candidates:
        return []

    best_indexes: list[int] = []
    best_score: float | None = None

    for anchor_index, anchor_bbox, anchor_gap in candidates:
        matched_indexes = {anchor_index}
        group_rect = fitz.Rect(anchor_bbox)
        changed = True

        while changed:
            changed = False
            for index, region_bbox, _ in candidates:
                if index in matched_indexes:
                    continue
                if _is_table_region_adjacent(group_rect, region_bbox):
                    matched_indexes.add(index)
                    group_rect |= region_bbox
                    changed = True

        indexes = sorted(matched_indexes)
        score = _score_table_group(indexes, regions) - (anchor_gap * 0.02)
        if best_score is None or score > best_score:
            best_indexes = indexes
            best_score = score

    return best_indexes


def _has_horizontal_match(image_bbox: fitz.Rect, caption_bbox: fitz.Rect) -> bool:
    overlap = min(image_bbox.x1, caption_bbox.x1) - max(image_bbox.x0, caption_bbox.x0)
    min_width = max(1.0, min(image_bbox.width, caption_bbox.width))
    overlap_ratio = max(0.0, overlap) / min_width

    if overlap_ratio >= 0.25:
        return True

    caption_center = (caption_bbox.x0 + caption_bbox.x1) / 2
    return (image_bbox.x0 - 30) <= caption_center <= (image_bbox.x1 + 30)


def _union_rects(rects: list[fitz.Rect]) -> fitz.Rect:
    rect = fitz.Rect(rects[0])
    for current in rects[1:]:
        rect |= current
    return rect


def _expand_with_context_text(
    figure_rect: fitz.Rect,
    text_regions: list[fitz.Rect],
    caption_rect: fitz.Rect,
    page_rect: fitz.Rect,
) -> fitz.Rect:
    expanded = fitz.Rect(figure_rect)

    for text_rect in text_regions:
        if text_rect.y0 >= caption_rect.y0:
            continue

        text_center_x = (text_rect.x0 + text_rect.x1) / 2
        text_center_y = (text_rect.y0 + text_rect.y1) / 2
        if not (
            (expanded.x0 - CONTEXT_TEXT_GAP) <= text_center_x <= (expanded.x1 + CONTEXT_TEXT_GAP)
            and (expanded.y0 - CONTEXT_TEXT_GAP) <= text_center_y <= (expanded.y1 + CONTEXT_TEXT_GAP)
        ):
            continue

        horizontal_gap = _axis_gap(expanded.x0, expanded.x1, text_rect.x0, text_rect.x1)
        vertical_gap = _axis_gap(expanded.y0, expanded.y1, text_rect.y0, text_rect.y1)
        has_horizontal_overlap = _overlap_length(expanded.x0, expanded.x1, text_rect.x0, text_rect.x1) > 0
        has_vertical_overlap = _overlap_length(expanded.y0, expanded.y1, text_rect.y0, text_rect.y1) > 0

        if (has_horizontal_overlap and vertical_gap <= CONTEXT_TEXT_GAP) or (has_vertical_overlap and horizontal_gap <= CONTEXT_TEXT_GAP):
            expanded |= text_rect

    expanded.x0 = max(page_rect.x0, expanded.x0 - CLIP_PADDING)
    expanded.y0 = max(page_rect.y0, expanded.y0 - CLIP_PADDING)
    expanded.x1 = min(page_rect.x1, expanded.x1 + CLIP_PADDING)
    expanded.y1 = min(page_rect.y1, expanded.y1 + CLIP_PADDING)
    return expanded


def _constrain_to_caption_column(figure_rect: fitz.Rect, caption_rect: fitz.Rect, page_rect: fitz.Rect) -> fitz.Rect:
    caption_width_ratio = caption_rect.width / max(1.0, page_rect.width)
    if caption_width_ratio >= 0.6:
        return figure_rect

    constrained = fitz.Rect(figure_rect)
    constrained.x0 = max(page_rect.x0, min(constrained.x0, caption_rect.x0 - CLIP_PADDING))
    constrained.x1 = min(constrained.x1, min(page_rect.x1, caption_rect.x1 + 12))
    return constrained


def _constrain_table_to_caption_column(table_rect: fitz.Rect, caption_rect: fitz.Rect, page_rect: fitz.Rect) -> fitz.Rect:
    constrained = fitz.Rect(table_rect)
    caption_width_ratio = caption_rect.width / max(1.0, page_rect.width)
    if caption_width_ratio < SINGLE_COLUMN_TABLE_RATIO:
        constrained.x0 = max(constrained.x0, max(page_rect.x0, caption_rect.x0 - 10))
        constrained.x1 = min(constrained.x1, min(page_rect.x1, caption_rect.x1 + 10))
    elif caption_width_ratio < 0.85:
        constrained.x0 = max(page_rect.x0, min(constrained.x0, caption_rect.x0 - TABLE_COLUMN_PADDING))
        constrained.x1 = min(page_rect.x1, max(constrained.x1, caption_rect.x1 + TABLE_COLUMN_PADDING))
    return constrained


def _pad_rect(rect: fitz.Rect, page_rect: fitz.Rect) -> fitz.Rect:
    padded = fitz.Rect(rect)
    padded.x0 = max(page_rect.x0, padded.x0 - CLIP_PADDING)
    padded.y0 = max(page_rect.y0, padded.y0 - CLIP_PADDING)
    padded.x1 = min(page_rect.x1, padded.x1 + CLIP_PADDING)
    padded.y1 = min(page_rect.y1, padded.y1 + CLIP_PADDING)
    return padded


def _dedupe_regions(regions: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    for region in regions:
        bbox = region["bbox"]
        if any(_is_nearly_same_rect(bbox, existing["bbox"]) for existing in deduped):
            continue
        deduped.append(region)
    return deduped


def _is_nearly_same_rect(a: fitz.Rect, b: fitz.Rect, tolerance: float = 6.0) -> bool:
    return (
        abs(a.x0 - b.x0) <= tolerance
        and abs(a.y0 - b.y0) <= tolerance
        and abs(a.x1 - b.x1) <= tolerance
        and abs(a.y1 - b.y1) <= tolerance
    )


def _is_region_adjacent(group_rect: fitz.Rect, candidate_rect: fitz.Rect) -> bool:
    horizontal_gap = _axis_gap(group_rect.x0, group_rect.x1, candidate_rect.x0, candidate_rect.x1)
    vertical_gap = _axis_gap(group_rect.y0, group_rect.y1, candidate_rect.y0, candidate_rect.y1)
    horizontal_overlap = _overlap_length(group_rect.x0, group_rect.x1, candidate_rect.x0, candidate_rect.x1)
    vertical_overlap = _overlap_length(group_rect.y0, group_rect.y1, candidate_rect.y0, candidate_rect.y1)

    if horizontal_overlap > 0 and vertical_gap <= REGION_GROUP_GAP:
        return True
    if vertical_overlap > 0 and horizontal_gap <= REGION_GROUP_GAP:
        return True
    return False


def _is_table_region_adjacent(group_rect: fitz.Rect, candidate_rect: fitz.Rect) -> bool:
    horizontal_gap = _axis_gap(group_rect.x0, group_rect.x1, candidate_rect.x0, candidate_rect.x1)
    vertical_gap = _axis_gap(group_rect.y0, group_rect.y1, candidate_rect.y0, candidate_rect.y1)
    horizontal_overlap = _overlap_length(group_rect.x0, group_rect.x1, candidate_rect.x0, candidate_rect.x1)
    vertical_overlap = _overlap_length(group_rect.y0, group_rect.y1, candidate_rect.y0, candidate_rect.y1)

    if horizontal_overlap > 0 and vertical_gap <= TABLE_REGION_GROUP_GAP:
        return True
    if vertical_overlap > 0 and horizontal_gap <= REGION_GROUP_GAP:
        return True
    return False


def _extend_table_captions(caption_blocks: list[dict], blocks: list[dict]) -> list[dict]:
    text_blocks = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        text = _normalize_whitespace(_extract_block_text(block))
        if not text:
            continue
        text_blocks.append({
            "bbox": fitz.Rect(block["bbox"]),
            "text": text,
        })

    extended_captions = []
    for caption in caption_blocks:
        extended_bbox = fitz.Rect(caption["bbox"])
        extended_text = caption["text"]

        for block in text_blocks:
            bbox = block["bbox"]
            if _is_nearly_same_rect(bbox, caption["bbox"]):
                continue
            if bbox.y0 < extended_bbox.y1 - 1:
                continue
            if bbox.y0 - extended_bbox.y1 > 18:
                continue
            if bbox.width > min(140.0, extended_bbox.width * 0.33):
                continue
            if not _has_horizontal_match(bbox, extended_bbox):
                continue
            if TABLE_CAPTION_PATTERN.match(block["text"]):
                continue

            extended_bbox |= bbox
            extended_text = _normalize_whitespace(f"{extended_text} {block['text']}")

        extended_captions.append({
            "bbox": extended_bbox,
            "label": caption["label"],
            "text": extended_text,
        })

    return extended_captions


def _score_table_group(indexes: list[int], regions: list[dict]) -> float:
    score = 0.0
    text_regions = 0

    for index in indexes:
        region = regions[index]
        bbox = region["bbox"]
        kind = region["kind"]
        if kind == "drawing":
            score += 1.2
            continue

        text_regions += 1
        text = region.get("text", "")
        digit_ratio = _digit_ratio(text)
        paragraph_like = _is_paragraph_like(text, region.get("line_count", 0))

        score += 1.5
        if bbox.width >= 140:
            score += 0.8
        if digit_ratio >= 0.08:
            score += 1.4
        if region.get("line_count", 0) <= 3:
            score += 0.3
        if paragraph_like:
            score -= 2.5

    if text_regions >= 2:
        score += 1.2

    return score


def _digit_ratio(text: str) -> float:
    if not text:
        return 0.0
    digits = sum(1 for char in text if char.isdigit())
    return digits / len(text)


def _is_paragraph_like(text: str, line_count: int) -> bool:
    if not text:
        return False
    alpha_chars = sum(1 for char in text if char.isalpha())
    punctuation = sum(1 for char in text if char in ",.;:()[]")
    return len(text) >= 120 and alpha_chars > 40 and punctuation >= 2 and line_count >= 3


def _axis_gap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    if end_a < start_b:
        return start_b - end_a
    if end_b < start_a:
        return start_a - end_b
    return 0.0


def _overlap_length(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _render_rect_as_webp(page: fitz.Page, rect: fitz.Rect) -> tuple[bytes, int, int]:
    pixmap = page.get_pixmap(clip=rect, matrix=fitz.Matrix(RENDER_SCALE, RENDER_SCALE), alpha=False)
    mode = "RGB" if pixmap.n >= 3 else "L"
    image = Image.frombytes(mode, [pixmap.width, pixmap.height], pixmap.samples)
    image_bytes = _encode_webp(image)
    return image_bytes, image.width, image.height


def _encode_webp(image: Image.Image) -> bytes:
    from io import BytesIO

    output = BytesIO()
    image.save(output, format="WEBP", quality=90, method=6)
    return output.getvalue()
