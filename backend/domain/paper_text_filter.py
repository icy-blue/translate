from __future__ import annotations

from html.parser import HTMLParser
import io
import re

import fitz
from pypdf import PdfReader

FIGURE_CAPTION_PATTERN = re.compile(
    r"^\s*(?:fig(?:ure)?\.?\s*(?:\d+|[ivxlcdm]+)[a-z]?|图\s*\d+[a-z]?)(?:\s*[:.：。-]\s+|\s+[-–]\s+)",
    re.IGNORECASE,
)
TABLE_CAPTION_PATTERN = re.compile(
    r"^\s*(?:table\.?\s*(?:\d+|[ivxlcdm]+)[a-z]?|表\s*\d+[a-z]?)(?:\s*[:.：。-]\s+|\s+[-–]\s+)",
    re.IGNORECASE,
)
LEGEND_PATTERN = re.compile(r"^\s*(?:legend|notes?|source)\s*[:：]", re.IGNORECASE)
HTML_SKIP_CLASS_PATTERN = re.compile(r"(?:^|[_\-\s])(?:ltx_)?(?:figure|table|caption|legend)(?:$|[_\-\s])", re.IGNORECASE)
HTML_SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "math",
    "figure",
    "figcaption",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "td",
    "th",
}
HTML_BLOCK_TAGS = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li"}


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _is_caption(text: str) -> bool:
    return bool(FIGURE_CAPTION_PATTERN.match(text) or TABLE_CAPTION_PATTERN.match(text))


def _is_table_caption(text: str) -> bool:
    return bool(TABLE_CAPTION_PATTERN.match(text))


def _is_legend(text: str) -> bool:
    return bool(LEGEND_PATTERN.match(text))


def _is_table_like_line(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if "|" in stripped or "\t" in stripped:
        return True
    if re.search(r"\S\s{2,}\S", stripped):
        return True
    numeric_tokens = re.findall(r"(?:\d+(?:\.\d+)?%?|[+-]?\d+(?:\.\d+)?)", stripped)
    word_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_.+-]*", stripped)
    return len(numeric_tokens) >= 2 and len(word_tokens) <= 8


def _is_probable_table_row_after_caption(text: str) -> bool:
    stripped = _normalize_whitespace(text)
    if not stripped:
        return False
    if re.search(r"[;:?!。！？]", stripped):
        return False
    if stripped.endswith(".") and not re.search(r"\d\.$", stripped):
        return False
    tokens = stripped.split()
    return 2 <= len(tokens) <= 10


def _block_lines(block: dict) -> list[str]:
    lines: list[str] = []
    for line in block.get("lines", []) or []:
        parts = [str(span.get("text", "") or "") for span in line.get("spans", []) or []]
        text = _normalize_whitespace(" ".join(parts))
        if text:
            lines.append(text)
    return lines


def _is_table_like_block(lines: list[str], *, after_table_caption: bool = False) -> bool:
    if not lines:
        return False
    if after_table_caption and any(_is_table_like_line(line) for line in lines):
        return True
    if after_table_caption and len(lines) == 1 and _is_probable_table_row_after_caption(lines[0]):
        return True
    if len(lines) >= 3 and sum(1 for line in lines if _is_table_like_line(line)) >= 2:
        return True
    return False


def filter_plain_paper_text(text: str) -> str:
    """Remove standalone figure/table artifacts from already-extracted paper text."""
    output: list[str] = []
    after_table_caption_lines = 0
    after_any_caption_lines = 0

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        normalized = _normalize_whitespace(line)
        if not normalized:
            if output and output[-1] != "":
                output.append("")
            after_table_caption_lines = 0
            after_any_caption_lines = 0
            continue

        if _is_caption(normalized):
            after_table_caption_lines = 8 if _is_table_caption(normalized) else 0
            after_any_caption_lines = 3
            continue

        if after_any_caption_lines and _is_legend(normalized):
            after_any_caption_lines -= 1
            continue

        if after_table_caption_lines and (_is_table_like_line(raw_line) or _is_probable_table_row_after_caption(normalized)):
            after_table_caption_lines -= 1
            continue

        output.append(normalized)
        after_table_caption_lines = max(0, after_table_caption_lines - 1)
        after_any_caption_lines = max(0, after_any_caption_lines - 1)

    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


class _FilteredPaperHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_stack: list[bool] = []
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized_tag = tag.lower()
        should_skip = self._skip_depth > 0 or normalized_tag in HTML_SKIP_TAGS or _attrs_should_skip(attrs)
        self._skip_stack.append(should_skip)
        if should_skip:
            self._skip_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in HTML_BLOCK_TAGS and not self._skip_depth and not _attrs_should_skip(attrs):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        was_skipped = self._skip_stack.pop() if self._skip_stack else False
        if was_skipped and self._skip_depth:
            self._skip_depth -= 1
        if not was_skipped and tag.lower() in HTML_BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = _normalize_whitespace(data)
        if text:
            self._chunks.append(text)

    def text(self) -> str:
        return filter_plain_paper_text("\n".join(self._chunks))


def _attrs_should_skip(attrs: list[tuple[str, str | None]]) -> bool:
    for name, value in attrs:
        if name.lower() not in {"class", "id", "role", "aria-label"}:
            continue
        if value and HTML_SKIP_CLASS_PATTERN.search(value):
            return True
    return False


def filter_arxiv_html_text(html: str) -> str:
    parser = _FilteredPaperHtmlParser()
    parser.feed(str(html or ""))
    return parser.text()


def _raw_pdf_text_from_bytes(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    page_text = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(part for part in page_text if part)


def extract_filtered_pdf_text(content: bytes) -> str:
    try:
        text = _filtered_pdf_text_with_fitz(content)
    except Exception:
        text = filter_plain_paper_text(_raw_pdf_text_from_bytes(content))
    if not text.strip():
        raise RuntimeError("DeepSeek provider requires extractable PDF text; this PDF may be scanned or image-based.")
    return text


def _filtered_pdf_text_with_fitz(content: bytes) -> str:
    document = fitz.open(stream=content, filetype="pdf")
    chunks: list[str] = []
    try:
        for page_number in range(document.page_count):
            page = document[page_number]
            blocks = [
                block
                for block in page.get_text("dict").get("blocks", [])
                if block.get("type") == 0
            ]
            blocks.sort(key=lambda block: (block.get("bbox", [0, 0, 0, 0])[1], block.get("bbox", [0, 0, 0, 0])[0]))
            after_table_caption_blocks = 0
            after_any_caption_blocks = 0
            page_chunks: list[str] = []

            for block in blocks:
                lines = _block_lines(block)
                text = _normalize_whitespace(" ".join(lines))
                if not text:
                    continue
                if _is_caption(text):
                    after_table_caption_blocks = 4 if _is_table_caption(text) else 0
                    after_any_caption_blocks = 2
                    continue
                if after_any_caption_blocks and _is_legend(text):
                    after_any_caption_blocks -= 1
                    continue
                if _is_table_like_block(lines, after_table_caption=after_table_caption_blocks > 0):
                    after_table_caption_blocks = max(0, after_table_caption_blocks - 1)
                    continue
                page_chunks.append(text)
                after_table_caption_blocks = max(0, after_table_caption_blocks - 1)
                after_any_caption_blocks = max(0, after_any_caption_blocks - 1)

            if page_chunks:
                chunks.append("\n\n".join(page_chunks))
    finally:
        document.close()
    return filter_plain_paper_text("\n\n".join(chunks))
