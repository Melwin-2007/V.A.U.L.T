from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import os
from pathlib import Path
import re
from typing import Any, Iterable, cast

from lxml import html as lxml_html
import pandas as pd

from data_verify.source_grounding import normalize_snippet
from edgar._filings import get_by_accession_number
from edgar.entity import Company
from edgar.financials import Financials
from edgar.settings import set_identity
from edgar.xbrl import XBRL
from segmentation.confidence import (
    detect_table_unit_and_period,
    format_scaled_number,
    has_accounting_zero_marker,
)
from segmentation.models import UnitContext

_SINGLE_SEGMENT_TEXT_PATTERNS = (
    re.compile(r"\b(?:one|single)\s+(?:operating|reportable)\s+segment\b", re.IGNORECASE),
    re.compile(r"\boperates?\s+(?:in|as)\s+(?:one|a\s+single)\s+(?:operating|reportable)?\s*segment\b", re.IGNORECASE),
    re.compile(r"\bmanage[sd]?\s+(?:its\s+)?(?:business|operations)\s+as\s+(?:one|a\s+single)\s+segment\b", re.IGNORECASE),
)

_REVENUE_CONCEPTS = (
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:Revenues",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap:SalesRevenueNet",
    "us-gaap:SalesRevenueGoodsNet",
    "us-gaap:SalesRevenueServicesNet",
    "us-gaap:InterestAndDividendIncomeOperating",
    "us-gaap:TotalRevenuesAndOtherIncome",
)

_TOTAL_AGGREGATE_MEMBERS = frozenset(
    {
        "SegmentDomain",
        "OperatingSegmentsMember",
        "ReportableSegmentsMember",
        "TotalSegmentMember",
        "AllSegmentsMember",
        "ConsolidatedMember",
        "ProductsAndServicesDomain",
        "SegmentGeographicalDomain",
        "ConsolidationItemsDomain",
    }
)

_CORPORATE_KEYWORDS = (
    "corporate",
    "unallocated",
    "all other",
    "other segments",
    "reconciling",
    "headquarters",
    "nonsegment",
    "non-segment",
)

_ELIMINATION_KEYWORDS = (
    "elimination",
    "eliminations",
    "intersegment",
    "inter-segment",
)


@dataclass(frozen=True)
class HtmlTableBlock:
    """Parsed HTML table paired with its surrounding header/caption text."""

    section: str
    header_text: str
    rows: list[str]


@dataclass(frozen=True)
class RawMetricFact:
    """Raw extracted financial fact with optional prior-year comparison and source metadata."""

    metric: str
    label: str
    concept: str
    current_value: float
    prior_value: float | None
    period_label: str
    expected_year: str | None
    fallback_scale: float
    is_derived: bool
    s_label: float
    source_section_hint: str
    label_keywords: list[str]


@dataclass(frozen=True)
class RawSegmentFact:
    """Raw extracted segment or reconciling fact."""

    name: str
    category: str
    axis: str
    member: str
    concept: str
    current_value: float
    prior_value: float | None
    raw_xbrl_value: float
    period_label: str
    expected_year: str | None
    fallback_scale: float
    s_label: float


@dataclass
class ExtractedFilingData:
    """All extracted consolidated, balance-sheet, and segment inputs for validation."""

    cik: str
    company_name: str
    form: str
    accession_no: str
    filing_date: str
    period_of_report: str
    sec_url: str
    tables: list[HtmlTableBlock]
    consolidated_revenue: RawMetricFact
    total_assets: RawMetricFact | None
    total_liabilities: RawMetricFact | None
    stockholders_equity: RawMetricFact | None
    is_single_segment: bool
    single_segment_reason: str
    axis_used: str | None
    breakdown_type: str
    segment_items: list[RawSegmentFact] = field(default_factory=list)


def normalize_cik(cik_or_ticker: str) -> str:
    """Normalize numeric CIKs to 10-digit zero-padded form; leave tickers untouched."""
    cleaned = cik_or_ticker.strip()
    if cleaned.isdigit():
        return cleaned.zfill(10)
    return cleaned


def _scale_from_decimals(decimals: Any) -> float:
    if decimals is None or pd.isna(decimals):
        return 1e6
    text = str(decimals).strip()
    if text == "-6":
        return 1e6
    if text == "-3":
        return 1e3
    if text == "-9":
        return 1e9
    if text == "0":
        return 1.0
    return 1e6


def _extract_tables_from_html_doc(raw_html: str, default_section: str) -> list[HtmlTableBlock]:
    """Extract tables with preceding caption/header text from an HTML fragment or document."""
    if not raw_html or not raw_html.strip():
        return []
    try:
        parser = lxml_html.HTMLParser(encoding="utf-8")
        doc = lxml_html.fromstring(raw_html.encode("utf-8"), parser=parser)
    except Exception:
        return []

    blocks: list[HtmlTableBlock] = []
    table_nodes = cast(list[Any], doc.xpath("//table"))
    for table in table_nodes:
        tr_nodes = cast(list[Any], table.xpath(".//tr"))
        rows: list[str] = []
        header_parts: list[str] = []

        # Collect preceding text (checking both table siblings and parent-wrapper siblings).
        anchor = table
        preceding_chunks: list[str] = []
        for _ in range(2):
            prev = anchor.getprevious()
            steps = 0
            while prev is not None and steps < 3:
                txt = normalize_snippet(str(prev.text_content()))
                if txt:
                    preceding_chunks.append(txt[:250])
                prev = prev.getprevious()
                steps += 1
            if preceding_chunks or anchor.getparent() is None:
                break
            anchor = anchor.getparent()
        if preceding_chunks:
            header_parts.extend(reversed(preceding_chunks))

        for idx, tr in enumerate(tr_nodes):
            cells = [
                normalize_snippet(str(cell.text_content()))
                for cell in cast(list[Any], tr.xpath("./th|./td"))
            ]
            non_empty = [c for c in cells if c]
            if not non_empty:
                continue
            row_str = " | ".join(non_empty)
            rows.append(row_str)
            if len(rows) <= 10 or tr.xpath("./th"):
                header_parts.append(row_str)

        if rows:
            blocks.append(
                HtmlTableBlock(
                    section=default_section,
                    header_text=" | ".join(header_parts),
                    rows=rows,
                )
            )
    return blocks


def _extract_tables_from_xbrl_textblocks(xb: XBRL) -> list[HtmlTableBlock]:
    """Extract HTML tables embedded inside XBRL TextBlock facts (Notes & Segment tables)."""
    blocks: list[HtmlTableBlock] = []
    tb_df = xb.query().by_concept("TextBlock").to_dataframe()
    if tb_df.empty:
        return blocks

    seen_values: set[int] = set()
    for _, row in tb_df.iterrows():
        val = row.get("value")
        if not isinstance(val, str) or "<table" not in val.lower():
            continue
        sig = hash(val)
        if sig in seen_values:
            continue
        seen_values.add(sig)
        label = str(row.get("label") or row.get("concept") or "XBRL Note")
        blocks.extend(_extract_tables_from_html_doc(val, f"Note - {label}"))
    return blocks


def _extract_tables_from_rendered_statements(xb: XBRL) -> list[HtmlTableBlock]:
    """Fallback table extraction from XBRL-rendered core statements when filing HTML is absent."""
    blocks: list[HtmlTableBlock] = []
    for stmt_name in ("IncomeStatement", "BalanceSheet"):
        try:
            stmt = xb.statements.get(stmt_name)
            if stmt is None:
                continue
            md = stmt.to_markdown()
            header_lines: list[str] = []
            for line in md.splitlines():
                if line.strip().startswith("| ---"):
                    break
                cleaned = normalize_snippet(line.replace("*", "").replace("#", ""))
                if cleaned:
                    header_lines.append(cleaned)

            rendered = stmt.render()
            if rendered is None:
                continue
            rows: list[str] = []
            for r in getattr(rendered, "rows", []) or []:
                label = normalize_snippet(str(getattr(r, "label", "") or ""))
                cell_texts = [
                    normalize_snippet(str(c.get_formatted_value()))
                    for c in (getattr(r, "cells", []) or [])
                    if c is not None and getattr(c, "value", None) is not None
                ]
                if label and cell_texts:
                    rows.append(" | ".join([label, *cell_texts]))
            if rows:
                blocks.append(
                    HtmlTableBlock(
                        section=f"Statement - {stmt_name}",
                        header_text=" | ".join(header_lines),
                        rows=rows,
                    )
                )
        except Exception:
            continue
    return blocks


def find_matching_snippet_and_unit(
    tables: list[HtmlTableBlock],
    *,
    label_keywords: Iterable[str],
    raw_value: float,
    fallback_scale: float,
    expected_year: str | None,
    default_section: str,
) -> tuple[str, str, UnitContext]:
    """Locate the best verbatim table row (supporting row-grouped and columnar tables) and UnitContext."""
    keywords = [kw.lower() for kw in label_keywords if kw]
    tokens_to_check: list[str] = []
    for scale in dict.fromkeys([fallback_scale, 1e6, 1e3, 1e9, 1.0]):
        disp, _ = format_scaled_number(raw_value, scale)
        tokens_to_check.append(disp)
        if raw_value < 0:
            abs_disp, _ = format_scaled_number(abs(raw_value), scale)
            tokens_to_check.append(f"({abs_disp})")

    def _has_exact_token(text: str) -> bool:
        if any(
            re.search(rf"(?<![\d,.]){re.escape(tok)}(?![\d,.])", text) is not None
            for tok in tokens_to_check
        ):
            return True
        if raw_value == 0.0 and has_accounting_zero_marker(text):
            return True
        return False

    best_match: tuple[int, str, str, HtmlTableBlock] | None = None
    for block in tables:
        hdr_lower = block.header_text.lower()
        for idx, row in enumerate(block.rows):
            if not _has_exact_token(row):
                continue
            row_lower = row.lower()
            row_hits = sum(1 for kw in keywords if kw in row_lower)
            if row_hits > 0:
                score = (row_hits * 20) + (10 if row_hits == len(keywords) else 0)
                snippet = row
            else:
                # Support grouped rows (e.g. "Americas:" followed by "Net sales | $ | 162,560")
                # and columnar headers (e.g. "Compute & Networking | Graphics" above "Revenue | 193,479").
                prev_rows = block.rows[max(0, idx - 3) : idx]
                matched_prev = next(
                    (pr for pr in reversed(prev_rows) if keywords and all(kw in pr.lower() for kw in keywords)),
                    None,
                )
                if matched_prev is not None:
                    score = 15
                    snippet = f"{matched_prev} :: {row}"
                elif keywords and all(kw in hdr_lower for kw in keywords):
                    score = 12
                    snippet = f"{block.header_text[:160]} :: {row}"
                else:
                    continue

            if "note -" in block.section.lower():
                score += 2
            if best_match is None or score > best_match[0]:
                best_match = (score, snippet, block.section, block)

    if best_match is not None:
        _, row_text, section_name, block = best_match
        unit_ctx = detect_table_unit_and_period(
            [block.header_text, block.section],
            expected_year=expected_year,
            fallback_scale_divisor=fallback_scale,
        )
        return row_text, section_name, unit_ctx

    # Fallback: match any row containing keywords even if digits differ (to reveal mismatches).
    for block in tables:
        for row in block.rows:
            row_lower = row.lower()
            if keywords and all(kw in row_lower for kw in keywords):
                unit_ctx = detect_table_unit_and_period(
                    [block.header_text, block.section],
                    expected_year=expected_year,
                    fallback_scale_divisor=fallback_scale,
                )
                return row, block.section, unit_ctx

    fallback_unit = detect_table_unit_and_period(
        [],
        expected_year=expected_year,
        fallback_scale_divisor=fallback_scale,
    )
    return "", default_section, fallback_unit


def _period_days(row: pd.Series) -> int | None:
    start = row.get("period_start")
    end = row.get("period_end")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except ValueError:
        return None


def _select_annual_period_ends(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """Identify the latest and prior annual fiscal period_end dates in a fact frame."""
    if df.empty or "period_end" not in df.columns:
        return None, None
    work = df.copy()
    work["_days"] = work.apply(_period_days, axis=1)
    annual = work[work["_days"].notna() & (work["_days"] >= 300)]
    target_df = annual if not annual.empty else work
    ends = sorted(
        {str(v) for v in target_df["period_end"].dropna().tolist()},
        reverse=True,
    )
    if not ends:
        return None, None
    return ends[0], (ends[1] if len(ends) > 1 else None)


def _select_instant_dates(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """Identify the latest and prior balance-sheet instant dates."""
    if df.empty or "period_instant" not in df.columns:
        return None, None
    dates = sorted(
        {str(v) for v in df["period_instant"].dropna().tolist()},
        reverse=True,
    )
    if not dates:
        return None, None
    return dates[0], (dates[1] if len(dates) > 1 else None)


def _extract_non_dim_fact(
    xb: XBRL,
    concepts: Iterable[str],
    *,
    period_type: str,
    metric_name: str,
    default_label: str,
    section_hint: str,
    label_keywords: list[str],
) -> RawMetricFact | None:
    """Extract a non-dimensional primary-statement fact for current and prior fiscal years."""
    all_facts = xb.query(include_dimensions=False).to_dataframe()
    if all_facts.empty:
        return None
    if "is_dimensioned" in all_facts.columns:
        all_facts = all_facts[all_facts["is_dimensioned"] == False]  # noqa: E712

    for concept in concepts:
        sub = all_facts[
            (all_facts["concept"] == concept)
            & (all_facts["numeric_value"].notna())
        ]
        if sub.empty:
            continue
        if period_type == "duration":
            cur_end, prev_end = _select_annual_period_ends(sub)
            if cur_end is None:
                continue
            cur_rows = sub[sub["period_end"] == cur_end]
            annual_cur = cur_rows[cur_rows.apply(lambda r: (_period_days(r) or 0) >= 300, axis=1)]
            if not annual_cur.empty:
                cur_rows = annual_cur
            if cur_rows.empty:
                cur_rows = sub[sub["period_end"] == cur_end]
            cur_row = cur_rows.iloc[0]
            prior_val: float | None = None
            if prev_end is not None:
                prev_rows = sub[sub["period_end"] == prev_end]
                prev_annual = prev_rows[prev_rows.apply(lambda r: (_period_days(r) or 0) >= 300, axis=1)]
                chosen_prev = prev_annual if not prev_annual.empty else prev_rows
                if not chosen_prev.empty:
                    prior_val = float(chosen_prev.iloc[0]["numeric_value"])
            expected_year = cur_end[:4]
            period_label = f"FY ended {cur_end}"
        else:
            cur_inst, prev_inst = _select_instant_dates(sub)
            if cur_inst is None:
                continue
            cur_row = sub[sub["period_instant"] == cur_inst].iloc[0]
            prior_val = None
            if prev_inst is not None:
                prev_rows = sub[sub["period_instant"] == prev_inst]
                if not prev_rows.empty:
                    prior_val = float(prev_rows.iloc[0]["numeric_value"])
            expected_year = cur_inst[:4]
            period_label = f"As of {cur_inst}"

        return RawMetricFact(
            metric=metric_name,
            label=str(cur_row.get("label") or default_label),
            concept=concept,
            current_value=float(cur_row["numeric_value"]),
            prior_value=prior_val,
            period_label=period_label,
            expected_year=expected_year,
            fallback_scale=_scale_from_decimals(cur_row.get("decimals")),
            is_derived=False,
            s_label=1.0,
            source_section_hint=section_hint,
            label_keywords=label_keywords,
        )
    return None


def _extract_consolidated_metrics(xb: XBRL) -> tuple[
    RawMetricFact,
    RawMetricFact | None,
    RawMetricFact | None,
    RawMetricFact | None,
]:
    """Extract consolidated revenue plus Assets, Liabilities, and Stockholders' Equity."""
    fin = Financials(xb)
    metrics = fin.get_financial_metrics()

    rev = _extract_non_dim_fact(
        xb,
        _REVENUE_CONCEPTS,
        period_type="duration",
        metric_name="consolidated_revenue",
        default_label="Total Revenue",
        section_hint="Consolidated Statement of Income",
        label_keywords=["revenue", "net sales", "total revenues"],
    )
    if rev is None:
        rev_val = metrics.get("revenue")
        if rev_val is None:
            raise ValueError("Unable to extract Consolidated Revenue from filing XBRL.")
        rev = RawMetricFact(
            metric="consolidated_revenue",
            label="Total Revenue (Derived)",
            concept="derived",
            current_value=float(rev_val),
            prior_value=None,
            period_label="Latest FY",
            expected_year=None,
            fallback_scale=1e6,
            is_derived=True,
            s_label=0.6,
            source_section_hint="Consolidated Statement of Income",
            label_keywords=["revenue", "net sales"],
        )
    else:
        # Match label keywords tightly to the company's actual income statement line label.
        rev_kws = [rev.label.lower()] if rev.label else ["revenue", "net sales"]
        rev = RawMetricFact(
            metric=rev.metric,
            label=rev.label,
            concept=rev.concept,
            current_value=rev.current_value,
            prior_value=rev.prior_value,
            period_label=rev.period_label,
            expected_year=rev.expected_year,
            fallback_scale=rev.fallback_scale,
            is_derived=rev.is_derived,
            s_label=1.0,
            source_section_hint=rev.source_section_hint,
            label_keywords=rev_kws,
        )

    assets = _extract_non_dim_fact(
        xb,
        ["us-gaap:Assets"],
        period_type="instant",
        metric_name="total_assets",
        default_label="Total assets",
        section_hint="Consolidated Balance Sheet",
        label_keywords=["total assets"],
    )
    if assets is None and isinstance(metrics.get("total_assets"), (int, float)):
        assets = RawMetricFact(
            metric="total_assets",
            label="Total assets",
            concept="derived",
            current_value=float(metrics["total_assets"]),
            prior_value=None,
            period_label="Latest Balance Sheet",
            expected_year=rev.expected_year,
            fallback_scale=rev.fallback_scale,
            is_derived=True,
            s_label=0.6,
            source_section_hint="Consolidated Balance Sheet",
            label_keywords=["total assets"],
        )

    liabilities = _extract_non_dim_fact(
        xb,
        ["us-gaap:Liabilities"],
        period_type="instant",
        metric_name="total_liabilities",
        default_label="Total liabilities",
        section_hint="Consolidated Balance Sheet",
        label_keywords=["total liabilities"],
    )
    equity = _extract_non_dim_fact(
        xb,
        [
            "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "us-gaap:StockholdersEquity",
            "us-gaap:PartnersCapital",
        ],
        period_type="instant",
        metric_name="stockholders_equity",
        default_label="Total stockholders' equity",
        section_hint="Consolidated Balance Sheet",
        label_keywords=["equity"],
    )
    if liabilities is None:
        # Mirror Example B: if Total Liabilities wasn't printed verbatim, derive from printed rows.
        cur_liab = _extract_non_dim_fact(
            xb,
            ["us-gaap:LiabilitiesCurrent"],
            period_type="instant",
            metric_name="current_liabilities",
            default_label="Total current liabilities",
            section_hint="Consolidated Balance Sheet",
            label_keywords=["current liabilities"],
        )
        noncur_liab = _extract_non_dim_fact(
            xb,
            ["us-gaap:LiabilitiesNoncurrent"],
            period_type="instant",
            metric_name="noncurrent_liabilities",
            default_label="Total non-current liabilities",
            section_hint="Consolidated Balance Sheet",
            label_keywords=["non-current liabilities"],
        )
        liab_and_eq = _extract_non_dim_fact(
            xb,
            ["us-gaap:LiabilitiesAndStockholdersEquity"],
            period_type="instant",
            metric_name="liabilities_and_equity",
            default_label="Total liabilities and stockholders' equity",
            section_hint="Consolidated Balance Sheet",
            label_keywords=["liabilities and"],
        )
        derived_liab = metrics.get("total_liabilities")
        if cur_liab is not None and noncur_liab is not None:
            prior_sum = (
                (cur_liab.prior_value + noncur_liab.prior_value)
                if (cur_liab.prior_value is not None and noncur_liab.prior_value is not None)
                else None
            )
            liabilities = RawMetricFact(
                metric="total_liabilities",
                label="Total liabilities (Current + Non-current)",
                concept="derived:LiabilitiesCurrent+LiabilitiesNoncurrent",
                current_value=cur_liab.current_value + noncur_liab.current_value,
                prior_value=prior_sum,
                period_label=cur_liab.period_label,
                expected_year=cur_liab.expected_year,
                fallback_scale=cur_liab.fallback_scale,
                is_derived=True,
                s_label=0.6,
                source_section_hint="Consolidated Balance Sheet",
                label_keywords=["current liabilities"],
            )
        elif liab_and_eq is not None and equity is not None:
            prior_derived = (
                (liab_and_eq.prior_value - equity.prior_value)
                if (liab_and_eq.prior_value is not None and equity.prior_value is not None)
                else None
            )
            liabilities = RawMetricFact(
                metric="total_liabilities",
                label="Total liabilities (Derived from Balance Sheet)",
                concept="derived:LiabilitiesAndStockholdersEquity-TotalEquity",
                current_value=liab_and_eq.current_value - equity.current_value,
                prior_value=prior_derived,
                period_label=liab_and_eq.period_label,
                expected_year=liab_and_eq.expected_year,
                fallback_scale=liab_and_eq.fallback_scale,
                is_derived=True,
                s_label=0.6,
                source_section_hint="Consolidated Balance Sheet",
                label_keywords=["liabilities"],
            )
        elif isinstance(derived_liab, (int, float)):
            liabilities = RawMetricFact(
                metric="total_liabilities",
                label="Total liabilities (Derived)",
                concept="derived",
                current_value=float(derived_liab),
                prior_value=None,
                period_label=assets.period_label if assets else "Latest Balance Sheet",
                expected_year=assets.expected_year if assets else rev.expected_year,
                fallback_scale=assets.fallback_scale if assets else rev.fallback_scale,
                is_derived=True,
                s_label=0.6,
                source_section_hint="Consolidated Balance Sheet",
                label_keywords=["liabilities"],
            )
    if equity is None and isinstance(metrics.get("stockholders_equity"), (int, float)):
        equity = RawMetricFact(
            metric="stockholders_equity",
            label="Total stockholders' equity",
            concept="derived",
            current_value=float(metrics["stockholders_equity"]),
            prior_value=None,
            period_label=assets.period_label if assets else "Latest Balance Sheet",
            expected_year=assets.expected_year if assets else rev.expected_year,
            fallback_scale=assets.fallback_scale if assets else rev.fallback_scale,
            is_derived=True,
            s_label=0.6,
            source_section_hint="Consolidated Balance Sheet",
            label_keywords=["equity"],
        )

    return rev, assets, liabilities, equity


def _member_short_name(member: str) -> str:
    if ":" in member:
        return member.split(":", 1)[1]
    if "_" in member:
        return member.split("_", 1)[1]
    return member


def _classify_segment_member(member: str, label: str) -> str:
    lower_name = f"{member} {label}".lower()
    if any(kw in lower_name for kw in _ELIMINATION_KEYWORDS):
        # Handle combined labels like "Eliminations and Other" vs pure eliminations.
        if "other" in lower_name and "intersegment" not in lower_name:
            return "corporate_other"
        return "intersegment_elimination"
    if any(kw in lower_name for kw in _CORPORATE_KEYWORDS):
        return "corporate_other"
    return "segment"


def _parent_members_in_xbrl(xb: XBRL) -> dict[str, set[str]]:
    """Map parent member short-names to child member short-names using XBRL domains."""
    parent_to_children: dict[str, set[str]] = {}
    domains = getattr(getattr(xb, "parser", None), "domains", None) or {}
    for domain_key, domain_obj in domains.items():
        members = getattr(domain_obj, "members", None) or []
        if members:
            p_short = _member_short_name(str(domain_key))
            parent_to_children.setdefault(p_short, set()).update(
                _member_short_name(str(m)) for m in members
            )
    return parent_to_children


def _check_single_segment(xb: XBRL, target_period_end: str | None) -> tuple[bool, str]:
    """Detect whether the company reports as a single operating/reportable segment."""
    for concept_query in ("NumberOfOperatingSegments", "NumberOfReportableSegments"):
        cnt_df = xb.query().by_concept(concept_query).to_dataframe()
        if cnt_df.empty:
            continue
        sub = cnt_df[cnt_df["numeric_value"].notna()]
        if target_period_end and "period_end" in sub.columns:
            pe_sub = sub[sub["period_end"] == target_period_end]
            if not pe_sub.empty:
                sub = pe_sub
        if not sub.empty:
            val = float(sub.iloc[0]["numeric_value"])
            if val == 1.0:
                return True, f"XBRL {sub.iloc[0]['concept']} = 1 (Single Segment)."

    tb_df = xb.query().by_concept("SegmentReportingDisclosureTextBlock").to_dataframe()
    if not tb_df.empty:
        raw_text = normalize_snippet(
            lxml_html.fromstring(str(tb_df.iloc[0]["value"]).encode("utf-8")).text_content()
            if "<" in str(tb_df.iloc[0]["value"])
            else str(tb_df.iloc[0]["value"])
        )
        for pat in _SINGLE_SEGMENT_TEXT_PATTERNS:
            m = pat.search(raw_text)
            if m:
                return True, f"Segment Note states '{m.group(0)}'."
    return False, ""


def _extract_segment_items_for_axis(
    xb: XBRL,
    *,
    axis_name: str,
    consolidated_concept: str,
    target_period_end: str | None,
    prior_period_end: str | None,
    expected_year: str | None,
    fallback_scale: float,
) -> list[RawSegmentFact]:
    """Extract leaf segment & reconciling items for a given XBRL axis and period."""
    q = xb.query(include_dimensions=True).by_dimension(axis_name)
    df = q.to_dataframe()
    if df.empty:
        return []

    rev_df = df[
        df["concept"].str.contains("Revenue|Sales", case=False, na=False)
        & ~df["concept"].str.contains("IntersegmentRevenue|Deferred|Remaining|Unrecognized", case=False, na=False)
        & df["numeric_value"].notna()
    ].copy()
    if rev_df.empty:
        return []

    if target_period_end:
        rev_df = rev_df[rev_df["period_end"] == target_period_end]
    if rev_df.empty:
        return []

    rev_df["_days"] = rev_df.apply(_period_days, axis=1)
    annual_df = rev_df[rev_df["_days"].notna() & (rev_df["_days"] >= 300)]
    if not annual_df.empty:
        rev_df = annual_df

    # Avoid sub-segment cross-dimensional double counting (keep only target axis, or + ConsolidationItemsAxis).
    dim_cols = [c for c in rev_df.columns if c.startswith("dim_")]
    target_dim_col = next((c for c in dim_cols if axis_name in c), None)
    consol_dim_col = next((c for c in dim_cols if "ConsolidationItemsAxis" in c), None)
    other_dim_cols = [c for c in dim_cols if c not in {target_dim_col, consol_dim_col}]

    if target_dim_col:
        rev_df = rev_df[rev_df[target_dim_col].notna()]
    if other_dim_cols:
        pure_df = rev_df[rev_df[other_dim_cols].isna().all(axis=1)]
        if not pure_df.empty:
            rev_df = pure_df

    if consol_dim_col and consol_dim_col != target_dim_col:
        # Exclude intersegment sub-column rows when extracting main segment revenues unless member itself is reconciling.
        non_elim = rev_df[
            rev_df[consol_dim_col].isna()
            | rev_df[consol_dim_col].astype(str).str.contains("OperatingSegmentsMember", case=False, na=False)
        ]
        if not non_elim.empty:
            rev_df = non_elim

    # Prefer the consolidated revenue concept if present; otherwise pick the concept with highest coverage.
    concepts_present = rev_df["concept"].unique().tolist()
    chosen_concept = (
        consolidated_concept
        if consolidated_concept in concepts_present
        else next(
            (c for c in _REVENUE_CONCEPTS if c in concepts_present),
            rev_df.groupby("concept")["numeric_value"].count().idxmax(),
        )
    )
    cur_df = rev_df[rev_df["concept"] == chosen_concept].copy()
    if cur_df.empty:
        return []

    # Extract member identifier from the axis-specific dim_ column (accurate even on multi-axis facts).
    member_col = target_dim_col if target_dim_col else "member"
    cur_df["_member_id"] = cur_df[member_col].astype(str)
    cur_df["_member_short"] = cur_df["_member_id"].apply(_member_short_name)

    # Filter out domain/aggregate total members.
    cur_df = cur_df[~cur_df["_member_short"].isin(_TOTAL_AGGREGATE_MEMBERS)]
    cur_df = cur_df.drop_duplicates(subset=["_member_id"])

    # Exclude parent members whose child members are also present (prevents double counting, e.g. ProductMember + iPhoneMember).
    present_shorts = set(cur_df["_member_short"].tolist())
    parent_map = _parent_members_in_xbrl(xb)
    parents_to_drop = {
        p for p, children in parent_map.items() if p in present_shorts and (children & present_shorts)
    }
    if parents_to_drop:
        cur_df = cur_df[~cur_df["_member_short"].isin(parents_to_drop)]

    # Build prior-year lookup for the same axis + concept.
    prior_lookup: dict[str, float] = {}
    if prior_period_end:
        prior_df = df[
            (df["concept"] == chosen_concept)
            & (df["period_end"] == prior_period_end)
            & df["numeric_value"].notna()
        ].copy()
        if not prior_df.empty:
            prior_df["_days"] = prior_df.apply(_period_days, axis=1)
            p_annual = prior_df[prior_df["_days"].notna() & (prior_df["_days"] >= 300)]
            if not p_annual.empty:
                prior_df = p_annual
            if other_dim_cols:
                p_pure = prior_df[prior_df[other_dim_cols].isna().all(axis=1)]
                if not p_pure.empty:
                    prior_df = p_pure
            if target_dim_col and target_dim_col in prior_df.columns:
                prior_df = prior_df[prior_df[target_dim_col].notna()]
                for _, pr in prior_df.iterrows():
                    prior_lookup[str(pr[target_dim_col])] = float(pr["numeric_value"])

    # Resolve human-readable labels for members.
    items: list[RawSegmentFact] = []
    for _, row in cur_df.iterrows():
        member_id = str(row["_member_id"])
        raw_label = str(row.get("dimension_member_label") or row.get("label") or _member_short_name(member_id))
        if raw_label == _member_short_name(member_id) and member_id in xb.parser.element_catalog:
            labels = xb.parser.element_catalog[member_id].labels
            if labels:
                raw_label = next(iter(labels.values()))
        # Clean camelCase or "SegmentMember" fallback labels if needed.
        if raw_label == member_id or ":" in raw_label:
            short = _member_short_name(member_id).removesuffix("SegmentMember").removesuffix("Member")
            raw_label = re.sub(r"([a-z])([A-Z])", r"\1 \2", short).strip()

        raw_val = float(row["numeric_value"])
        category = _classify_segment_member(member_id, raw_label)

        # For intersegment eliminations, formula subtracts positive elimination magnitude.
        if category == "intersegment_elimination":
            normalized_val = abs(raw_val)
            prior_raw = prior_lookup.get(member_id)
            normalized_prior = abs(prior_raw) if prior_raw is not None else None
        else:
            normalized_val = raw_val
            normalized_prior = prior_lookup.get(member_id)

        items.append(
            RawSegmentFact(
                name=raw_label,
                category=category,
                axis=axis_name,
                member=member_id,
                concept=chosen_concept,
                current_value=normalized_val,
                prior_value=normalized_prior,
                raw_xbrl_value=raw_val,
                period_label=f"FY ended {target_period_end}" if target_period_end else "Latest FY",
                expected_year=expected_year,
                fallback_scale=_scale_from_decimals(row.get("decimals")) or fallback_scale,
                s_label=1.0,
            )
        )

    # Deduplicate Disney-style duplicate elimination alias (e.g. EliminationsAndOtherMember == SegmentEliminationsMember).
    seen_elims: set[tuple[str, float]] = set()
    deduped: list[RawSegmentFact] = []
    for item in items:
        if item.category in {"corporate_other", "intersegment_elimination"} and item.raw_xbrl_value < 0:
            key = ("neg_reconcile", item.raw_xbrl_value)
            if key in seen_elims:
                continue
            seen_elims.add(key)
        deduped.append(item)

    return deduped


def _extract_consolidation_reconciling_items(
    xb: XBRL,
    *,
    consolidated_concept: str,
    target_period_end: str | None,
    prior_period_end: str | None,
    expected_year: str | None,
    fallback_scale: float,
    existing_members: set[str],
) -> list[RawSegmentFact]:
    """Check ConsolidationItemsAxis for Corporate/Unallocated or Intersegment Eliminations."""
    items = _extract_segment_items_for_axis(
        xb,
        axis_name="ConsolidationItemsAxis",
        consolidated_concept=consolidated_concept,
        target_period_end=target_period_end,
        prior_period_end=prior_period_end,
        expected_year=expected_year,
        fallback_scale=fallback_scale,
    )
    return [
        it
        for it in items
        if it.category in {"corporate_other", "intersegment_elimination"}
        and it.member not in existing_members
    ]


def extract_from_xbrl(
    xb: XBRL,
    *,
    cik: str,
    company_name: str,
    form: str = "10-K",
    accession_no: str = "",
    filing_date: str = "",
    period_of_report: str = "",
    sec_url: str = "",
    raw_html: str | None = None,
) -> ExtractedFilingData:
    """Extract consolidated financials, balance sheet metrics, and segment breakdowns."""
    tables: list[HtmlTableBlock] = []
    if raw_html:
        tables.extend(_extract_tables_from_html_doc(raw_html, "Form 10-K Primary HTML"))
    tables.extend(_extract_tables_from_xbrl_textblocks(xb))
    if not raw_html:
        tables.extend(_extract_tables_from_rendered_statements(xb))

    rev, assets, liabilities, equity = _extract_consolidated_metrics(xb)

    # Determine target current & prior annual period_end dates from revenue facts.
    non_dim = xb.query(include_dimensions=False).by_concept(rev.concept).to_dataframe()
    target_period_end, prior_period_end = _select_annual_period_ends(non_dim)

    is_single_seg, single_reason = _check_single_segment(xb, target_period_end)

    # Try Business Segments first; fallback to Product/Service or Geographic if none found.
    segment_items: list[RawSegmentFact] = []
    axis_used: str | None = None
    breakdown_type = "None"

    axis_candidates = [
        ("StatementBusinessSegmentsAxis", "Business / Operating Segments"),
        ("ProductOrServiceAxis", "Product / Service Segments"),
        ("StatementGeographicalAxis", "Geographic Segments"),
    ]
    for axis_name, b_type in axis_candidates:
        found = _extract_segment_items_for_axis(
            xb,
            axis_name=axis_name,
            consolidated_concept=rev.concept,
            target_period_end=target_period_end,
            prior_period_end=prior_period_end,
            expected_year=rev.expected_year,
            fallback_scale=rev.fallback_scale,
        )
        # Only treat an axis as valid segment breakdown if it has >=2 segments or isn't overridden by single-segment rule.
        if found and not (is_single_seg and axis_name != "StatementBusinessSegmentsAxis"):
            segment_items = found
            axis_used = axis_name
            breakdown_type = b_type
            break

    if segment_items:
        existing = {it.member for it in segment_items}
        reconciling = _extract_consolidation_reconciling_items(
            xb,
            consolidated_concept=rev.concept,
            target_period_end=target_period_end,
            prior_period_end=prior_period_end,
            expected_year=rev.expected_year,
            fallback_scale=rev.fallback_scale,
            existing_members=existing,
        )
        segment_items.extend(reconciling)

    if is_single_seg and not segment_items:
        breakdown_type = "Single Operating Segment"
    elif not segment_items and not is_single_seg:
        is_single_seg = True
        single_reason = "No multi-segment revenue breakdown found in XBRL segment axes."
        breakdown_type = "Single / Unsegmented"

    return ExtractedFilingData(
        cik=cik,
        company_name=company_name,
        form=form,
        accession_no=accession_no,
        filing_date=filing_date,
        period_of_report=period_of_report or (target_period_end or ""),
        sec_url=sec_url,
        tables=tables,
        consolidated_revenue=rev,
        total_assets=assets,
        total_liabilities=liabilities,
        stockholders_equity=equity,
        is_single_segment=is_single_seg,
        single_segment_reason=single_reason,
        axis_used=axis_used,
        breakdown_type=breakdown_type,
        segment_items=segment_items,
    )


def extract_from_cik(
    cik: str,
    *,
    form: str = "10-K",
    identity: str | None = None,
) -> ExtractedFilingData:
    """Resolve latest 10-K for a CIK via edgartools and extract all validation data."""
    resolved_identity = identity or os.environ.get("EDGAR_IDENTITY") or "VAULT-Segmentation research@example.com"
    set_identity(resolved_identity)

    lookup = normalize_cik(cik)
    company = Company(lookup)
    filings = company.get_filings(form=form, amendments=False)
    filing = filings.latest() if filings else None
    if filing is None:
        all_filings = company.get_filings(form=form)
        filing = all_filings.latest() if all_filings else None
    if filing is None:
        raise ValueError(f"No {form} filing found for '{cik}'.")

    if hasattr(filing, "accession_no") and filing.accession_no is None:
        filing = get_by_accession_number(str(filing.accession_number))

    xb = filing.xbrl()
    if xb is None:
        raise ValueError(f"Filing {filing.accession_no} has no XBRL data.")

    raw_html = filing.html()
    tenk: Any = None
    try:
        tenk = filing.obj()
    except Exception:
        tenk = None

    period_of_report = str(getattr(tenk, "period_of_report", "") or getattr(filing, "period_of_report", "") or "")
    return extract_from_xbrl(
        xb,
        cik=str(company.cik).zfill(10),
        company_name=str(company.name),
        form=str(filing.form),
        accession_no=str(filing.accession_no),
        filing_date=str(filing.filing_date),
        period_of_report=period_of_report,
        sec_url=str(filing.filing_url),
        raw_html=raw_html,
    )


def extract_from_xbrl_dir(
    xbrl_dir: str | Path,
    *,
    cik: str = "0000000000",
    company_name: str = "Offline XBRL Company",
) -> ExtractedFilingData:
    """Load an offline XBRL directory fixture for deterministic testing."""
    path = Path(xbrl_dir)
    xb = XBRL.from_directory(path)
    entity_info = getattr(xb, "entity_info", {}) or {}
    resolved_name = str(entity_info.get("entity_name") or company_name)
    resolved_cik = str(entity_info.get("cik") or cik).zfill(10)
    return extract_from_xbrl(
        xb,
        cik=resolved_cik,
        company_name=resolved_name,
    )
