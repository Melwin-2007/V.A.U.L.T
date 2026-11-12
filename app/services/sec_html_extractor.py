"""Lightweight SEC 10-K HTML financial table extractor for CLI reporting."""

from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import Optional

import httpx

from app.models.yoy_models import YearMetricInput, YoYValidationRequest

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_NEGATIVE_PARENS_RE = re.compile(r"^\(\s*([^)]+?)\s*\)$")
_NUMERIC_RE = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^[+-]?\d+(?:\.\d+)?$")


class _TableHTMLParser(HTMLParser):
    """Extract tables as lists of cleaned cell rows."""

    def __init__(self) -> None:
        super().__init__()
        self._table_depth = 0
        self._in_row = False
        self._in_cell = False
        self.tables: list[list[list[str]]] = []
        self._curr_table: list[list[str]] = []
        self._curr_row: list[str] = []
        self._curr_cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_l = tag.lower()
        if tag_l == "table":
            if self._table_depth == 0:
                self._curr_table = []
            self._table_depth += 1
        elif tag_l == "tr" and self._table_depth == 1:
            self._in_row = True
            self._curr_row = []
        elif tag_l in ("td", "th") and self._in_row:
            self._in_cell = True
            self._curr_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag_l = tag.lower()
        if tag_l in ("td", "th") and self._in_cell:
            self._in_cell = False
            raw = html.unescape("".join(self._curr_cell))
            cleaned = " ".join(raw.split())
            self._curr_row.append(cleaned)
        elif tag_l == "tr" and self._in_row:
            self._in_row = False
            cells = [c for c in self._curr_row if c and c not in ("$", "€", "£", "¥")]
            if cells:
                self._curr_table.append(cells)
        elif tag_l == "table" and self._table_depth > 0:
            self._table_depth -= 1
            if self._table_depth == 0 and self._curr_table:
                self.tables.append(self._curr_table)

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._curr_cell.append(data)


def _parse_numeric_cell(cell: str) -> tuple[Optional[Decimal], bool]:
    """Parse an SEC table cell into (Decimal | None, is_percent_cell)."""
    text = cell.strip()
    if not text or text in ("—", "-", "--", "N/A"):
        return None, False
    is_pct = text.endswith("%")
    text = text.replace("$", "").replace("%", "").strip()
    is_negative = False
    paren_match = _NEGATIVE_PARENS_RE.match(text)
    if paren_match:
        is_negative = True
        text = paren_match.group(1).strip()
    text = text.replace(" ", "")
    if not _NUMERIC_RE.match(text):
        return None, False
    try:
        val = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None, False
    return (-val if is_negative else val), is_pct


def _detect_base_unit(header_text: str) -> str:
    lower = header_text.lower()
    if "in billions" in lower:
        return "USD billions"
    if "in thousands" in lower:
        return "USD thousands"
    if "in millions" in lower:
        return "USD millions"
    return "USD millions"


def _detect_row_unit(label: str, base_unit: str, in_shares_section: bool) -> str:
    lower = label.lower()
    if "per common share" in lower or "per share" in lower:
        return "USD per share"
    if (
        "as a percentage" in lower
        or "percentage change" in lower
        or "return on assets" in lower
        or "return on investment" in lower
        or "effective tax rate" in lower
    ):
        return "percent"
    if in_shares_section or "weighted-average" in lower or "shares outstanding" in lower:
        scale = base_unit.removeprefix("USD ").strip()
        return f"shares ({scale})" if scale else "shares"
    return base_unit


def _detect_company_name(raw_html: str) -> str:
    match = re.search(
        r"([A-Z][A-Za-z0-9.,&\s'-]{2,60}?)\s+Consolidated Statements of Income",
        re.sub(r"<[^>]+>", " ", raw_html[:1500000]),
    )
    if match:
        return " ".join(match.group(1).split())
    return "SEC Registrant"


def fetch_filing_html(url: str, timeout: float = 30.0) -> str:
    """Download an SEC 10-K HTML document."""
    clean_url = url.split("#", 1)[0]
    headers = {"User-Agent": "VAULT-YoY-Validator/1.0 (audit@example.com)"}
    response = httpx.get(
        clean_url, headers=headers, follow_redirects=True, timeout=timeout
    )
    response.raise_for_status()
    return response.text


def extract_yoy_requests_from_html(
    raw_html: str,
    threshold_percent: Decimal = Decimal("30"),
    company_override: Optional[str] = None,
    financial_statements_only: bool = True,
) -> list[YoYValidationRequest]:
    """Extract comparable multi-year metrics from consolidated financial tables."""
    parser = _TableHTMLParser()
    parser.feed(raw_html)

    company = company_override or _detect_company_name(raw_html)
    requests: list[YoYValidationRequest] = []
    seen_metrics: set[str] = set()

    for table in parser.tables:
        years: list[int] = []
        header_idx = -1
        base_unit = "USD millions"
        has_amounts_header = False

        for idx, row in enumerate(table[:8]):
            row_joined = " ".join(row)
            if "amounts in" in row_joined.lower():
                base_unit = _detect_base_unit(row_joined)
                has_amounts_header = True
            found_years = [int(c) for c in row if _YEAR_RE.match(c)]
            if len(found_years) >= 2:
                years = found_years
                header_idx = idx
                break

        if header_idx == -1 or len(years) < 2:
            continue
        if financial_statements_only and not has_amounts_header:
            continue

        # Expect latest reporting year first (e.g. [2026, 2025, 2024]), or handle ascending
        curr_year = max(years[0], years[1])
        prev_year = min(years[0], years[1])
        curr_pos = 0 if years[0] == curr_year else 1
        prev_pos = 1 if curr_pos == 0 else 0
        num_year_cols = len(years)

        in_shares_section = False
        for row in table[header_idx + 1 :]:
            if len(row) < 3:
                row_txt = " ".join(row).lower()
                if "weighted-average" in row_txt or "shares outstanding" in row_txt:
                    in_shares_section = True
                elif "assets" in row_txt or "liabilities" in row_txt or "cash flows" in row_txt:
                    in_shares_section = False
                continue

            label = row[0].strip().rstrip(":")
            if not label or label.lower().startswith("see accompanying"):
                continue
            if "weighted-average" in label.lower():
                in_shares_section = True

            numeric_candidates = [_parse_numeric_cell(c) for c in row[1:]]
            parsed_pairs = [(v, p) for v, p in numeric_candidates if v is not None]
            if len(parsed_pairs) < 2 or len(parsed_pairs) > num_year_cols + 1:
                continue

            curr_val, curr_is_pct = parsed_pairs[curr_pos]
            prev_val, prev_is_pct = parsed_pairs[prev_pos]

            metric_key = label.lower()
            if metric_key in seen_metrics:
                continue
            seen_metrics.add(metric_key)

            if curr_is_pct and prev_is_pct:
                unit = "percent"
            else:
                unit = _detect_row_unit(label, base_unit, in_shares_section)
            requests.append(
                YoYValidationRequest(
                    company=company,
                    metric=label,
                    current_year=YearMetricInput(
                        year=curr_year, value=curr_val, unit=unit
                    ),
                    previous_year=YearMetricInput(
                        year=prev_year, value=prev_val, unit=unit
                    ),
                    threshold_percent=threshold_percent,
                )
            )

    return requests
