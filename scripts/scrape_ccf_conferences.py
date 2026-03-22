#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_URL = "https://ccf.atom.im/"
DEFAULT_OUTPUT = Path("data/ccf_publications.json")
HEADER_ROWS = {
    ("序号", "会议简称", "会议全称", "分类", "类型", "专业领域"),
    ("序号", "刊物简称", "刊物全称", "分类", "类型", "专业领域"),
}
ROW_WIDTH = 6
TYPE_MAPPING = {
    "会议": "conference",
    "期刊": "journal",
}


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class CCFTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.div_depth = 0
        self.in_ccf_section = False
        self.table_depth = 0
        self.in_target_table = False
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)

        if tag == "div":
            if self.in_ccf_section:
                self.div_depth += 1
            elif attrs_dict.get("id") == "ccf":
                self.in_ccf_section = True
                self.div_depth = 1
            return

        if not self.in_ccf_section:
            return

        if tag == "table":
            self.table_depth += 1
            if not self.in_target_table:
                self.in_target_table = True
            return

        if not self.in_target_table:
            return

        if tag == "tr":
            self.in_row = True
            self.current_row = []
            return

        if tag in {"td", "th"} and self.in_row:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.in_ccf_section:
            self.div_depth -= 1
            if self.div_depth == 0:
                self.in_ccf_section = False
            return

        if not self.in_target_table:
            return

        if tag in {"td", "th"} and self.in_cell:
            self.in_cell = False
            self.current_row.append(normalize_text("".join(self.current_cell)))
            self.current_cell = []
            return

        if tag == "tr" and self.in_row:
            self.in_row = False
            if any(self.current_row):
                self.rows.append(self.current_row)
            self.current_row = []
            return

        if tag == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.in_target_table = False

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.current_cell.append(data)


def fetch_html(url: str, timeout: int) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_publications(html: str) -> list[dict[str, str]]:
    parser = CCFTableParser()
    parser.feed(html)
    parser.close()

    rows = [row for row in parser.rows if len(row) >= ROW_WIDTH]
    if not rows:
        raise ValueError("未在页面中找到目录表格数据。")

    publications: list[dict[str, str]] = []
    header_count = 0
    for row in rows:
        record = row[:ROW_WIDTH]
        if tuple(record) in HEADER_ROWS:
            header_count += 1
            continue

        publications.append(
            {
                "abbr": record[1],
                "full_name": record[2],
                "category": record[3],
                "type": TYPE_MAPPING.get(record[4], record[4]),
                "field": record[5],
            }
        )

    if header_count == 0:
        raise ValueError("页面表头与预期不一致，未匹配到目录表头。")

    if not publications:
        raise ValueError("页面解析成功，但没有提取到任何目录记录。")

    return publications


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="抓取 ccf.atom.im 上的 CCF 推荐会议和期刊目录并输出为 JSON。"
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"要抓取的页面地址，默认：{DEFAULT_URL}")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"输出 JSON 文件路径，默认：{DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="网络请求超时时间（秒），默认：30",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON 缩进空格数，默认：2",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        html = fetch_html(args.url, args.timeout)
        publications = parse_publications(html)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise SystemExit(f"抓取失败：{exc}") from exc
    except ValueError as exc:
        raise SystemExit(f"解析失败：{exc}") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(publications, ensure_ascii=False, indent=args.indent) + "\n",
        encoding="utf-8",
    )

    conference_count = sum(item["type"] == "conference" for item in publications)
    journal_count = sum(item["type"] == "journal" for item in publications)
    print(
        f"已写入 {len(publications)} 条目录记录到 {args.output} "
        f"(conference={conference_count}, journal={journal_count})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
