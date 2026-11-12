from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable, Literal

from lxml import html as lxml_html

# pyrefly: ignore[missing-import]
from edgar import Company, get_by_accession_number, set_identity


RiskLevel = Literal["LOW", "HIGH"]


@dataclass(frozen=True)
class GroundingResult:
    """Result of verifying extracted digits verbatim against a cited source snippet."""

    passed: bool
    confidence_score: float
    risk_level: RiskLevel
    hallucination_flag: bool
    matched_text: str | None
    reason: str


@dataclass(frozen=True)
class ExtractedField:
    """A single extracted metric paired with its cited raw source snippet and grounding verdict."""

    metric: str
    period: str
    extracted_value: float | int | str | None
    display_value: str
    unit: str
    source_section: str
    source_snippet: str
    grounding: GroundingResult


@dataclass(frozen=True)
class DerivedMetricResult:
    """Result of a derived calculation gated by upstream source-grounding verification."""

    metric: str
    formula: str
    value: float | None
    unit: str
    blocked: bool
    reason: str
    required_inputs: list[str]


def normalize_snippet(text: str) -> str:
    """Normalize whitespace and non-breaking spaces without altering digits or punctuation."""
    cleaned = text.replace("\xa0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def _candidate_tokens(display_value: str, numeric_value: float | int | None = None) -> list[str]:
    """Build exact verbatim numeric token candidates (handling accounting parentheses for negatives)."""
    raw = display_value.strip().lstrip("$")
    candidates: list[str] = []

    if raw:
        candidates.append(raw)
        if raw.startswith("-"):
            unsigned = raw[1:]
            candidates.append(f"({unsigned})")
            candidates.append(f"$({unsigned})")
            candidates.append(f"-${unsigned}")
        else:
            candidates.append(f"${raw}")

    if numeric_value is not None and isinstance(numeric_value, (int, float)):
        abs_val = abs(numeric_value)
        if float(abs_val).is_integer():
            int_val = int(abs_val)
            formatted = f"{int_val:,}"
            plain = str(int_val)
            for token in (formatted, plain):
                if numeric_value < 0:
                    candidates.extend([f"({token})", f"$({token})", f"-{token}", f"-${token}"])
                else:
                    candidates.extend([token, f"${token}"])

    seen: set[str] = set()
    unique: list[str] = []
    for token in candidates:
        if token and token not in seen:
            seen.add(token)
            unique.append(token)
    return unique


def verify_source_grounding(
    display_value: str,
    source_snippet: str,
    *,
    numeric_value: float | int | None = None,
) -> GroundingResult:
    """Verify verbatim digit presence in source_snippet; drop confidence to 0.0 on mismatch."""
    normalized_snippet = normalize_snippet(source_snippet)
    if not normalized_snippet:
        return GroundingResult(
            passed=False,
            confidence_score=0.0,
            risk_level="HIGH",
            hallucination_flag=True,
            matched_text=None,
            reason="Cited source_snippet is empty.",
        )

    for candidate in _candidate_tokens(display_value, numeric_value):
        escaped = re.escape(candidate)
        # Prevent partial digit matches such as "825" matching inside "29,825" or "2.7" inside "2.74".
        pattern = re.compile(rf"(?<![\d,.]){escaped}(?![\d,.])")
        match = pattern.search(normalized_snippet)
        if match:
            return GroundingResult(
                passed=True,
                confidence_score=1.0,
                risk_level="LOW",
                hallucination_flag=False,
                matched_text=match.group(0),
                reason=f"Verified verbatim match '{match.group(0)}' in cited source_snippet.",
            )

    return GroundingResult(
        passed=False,
        confidence_score=0.0,
        risk_level="HIGH",
        hallucination_flag=True,
        matched_text=None,
        reason=(
            f"Exact digits for '{display_value}' not found verbatim in cited source_snippet; "
            "confidence dropped to 0.0 and flagged as potential hallucination."
        ),
    )


def safe_compute_derived(
    metric: str,
    formula: str,
    unit: str,
    inputs: dict[str, ExtractedField],
    compute_fn: Callable[[dict[str, float]], float],
) -> DerivedMetricResult:
    """Execute downstream math only after all required inputs pass source grounding."""
    required_names = list(inputs.keys())
    failed = [name for name, field in inputs.items() if not field.grounding.passed]
    if failed:
        return DerivedMetricResult(
            metric=metric,
            formula=formula,
            value=None,
            unit=unit,
            blocked=True,
            reason=f"Math blocked: ungrounded input(s) {failed} failed Source Grounding Check.",
            required_inputs=required_names,
        )

    numeric_inputs: dict[str, float] = {}
    for name, field in inputs.items():
        if not isinstance(field.extracted_value, (int, float)):
            return DerivedMetricResult(
                metric=metric,
                formula=formula,
                value=None,
                unit=unit,
                blocked=True,
                reason=f"Math blocked: input '{name}' has non-numeric value {field.extracted_value!r}.",
                required_inputs=required_names,
            )
        numeric_inputs[name] = float(field.extracted_value)

    try:
        computed = compute_fn(numeric_inputs)
    except ZeroDivisionError as exc:
        return DerivedMetricResult(
            metric=metric,
            formula=formula,
            value=None,
            unit=unit,
            blocked=True,
            reason=f"Math blocked: division by zero ({exc}).",
            required_inputs=required_names,
        )

    return DerivedMetricResult(
        metric=metric,
        formula=formula,
        value=computed,
        unit=unit,
        blocked=False,
        reason="All inputs verified via Source Grounding Check prior to calculation.",
        required_inputs=required_names,
    )


def extract_table_rows_from_html(raw_html: str) -> list[str]:
    """Extract normalized table rows from raw SEC HTML for verbatim source-snippet citations."""
    doc = lxml_html.fromstring(raw_html.encode("utf-8"))
    rows: list[str] = []
    for tr in doc.xpath("//tr"):
        cells = [normalize_snippet(td.text_content()) for td in tr.xpath("./th|./td")]
        non_empty = [c for c in cells if c]
        if non_empty:
            rows.append(" | ".join(non_empty))
    return rows


def find_verbatim_row(
    rows: list[str],
    label_keywords: list[str],
    expected_token: str | None = None,
) -> str:
    """Locate the raw table row matching label keywords (preferring rows containing expected_token)."""
    lowered_keywords = [kw.lower() for kw in label_keywords]
    label_matches = [
        row for row in rows if all(kw in row.lower() for kw in lowered_keywords)
    ]
    if not label_matches:
        return ""
    if expected_token:
        for row in label_matches:
            if expected_token in row:
                return row
    return label_matches[0]


def _extract_snippet_around_text(full_text: str, anchor_phrase: str, radius: int = 360) -> str:
    """Extract a verbatim text snippet containing anchor_phrase from plain filing text."""
    normalized = normalize_snippet(full_text)
    idx = normalized.lower().find(anchor_phrase.lower())
    if idx == -1:
        return ""
    start = max(0, idx - 80)
    if start > 0:
        next_space = normalized.find(" ", start)
        if next_space != -1 and next_space < idx:
            start = next_space + 1
    end = min(len(normalized), idx + len(anchor_phrase) + radius)
    if end < len(normalized):
        last_space = normalized.rfind(" ", idx + len(anchor_phrase), end)
        if last_space != -1:
            end = last_space
    return normalized[start:end]


def build_grounded_field(
    *,
    metric: str,
    period: str,
    extracted_value: float | int | str | None,
    display_value: str,
    unit: str,
    source_section: str,
    source_snippet: str,
) -> ExtractedField:
    """Create an ExtractedField with an attached Source Grounding Check result."""
    numeric_val = extracted_value if isinstance(extracted_value, (int, float)) else None
    grounding = verify_source_grounding(
        display_value,
        source_snippet,
        numeric_value=numeric_val,
    )
    return ExtractedField(
        metric=metric,
        period=period,
        extracted_value=extracted_value,
        display_value=display_value,
        unit=unit,
        source_section=source_section,
        source_snippet=source_snippet,
        grounding=grounding,
    )


def extract_grounded_walmart_10k(
    accession_no: str = "0000104169-26-000055",
    identity: str = "Melwin melwinfernandes2007@gmail.com",
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Extract Walmart 10-K data via edgartools and verify every number via Source Grounding Check."""
    # `Company` imported at module top prevents edgar's ats->funds->entity circular import in v6.0.0.
    _ = Company
    set_identity(identity)

    filing = get_by_accession_number(accession_no)
    if filing is None:
        raise ValueError(f"SEC filing {accession_no} was not found.")

    tenk = filing.obj()
    if tenk is None or tenk.financials is None:
        raise ValueError(f"Filing {accession_no} did not return TenK financials.")

    raw_html = filing.html()
    if not raw_html:
        raise ValueError(f"Filing {accession_no} returned empty primary HTML.")

    rows = extract_table_rows_from_html(raw_html)
    metrics = tenk.financials.get_financial_metrics()
    item_9b_text = str(tenk["Item 9B"] or "")

    # Statement tables in Walmart's 10-K report values in millions.
    specs = [
        ("total_revenues", "FY2026 (Ended Jan 31, 2026)", round(metrics["revenue"] / 1e6), "USD millions", "Item 8 - Consolidated Statement of Income", ["Total revenues"], "713,163"),
        ("operating_income", "FY2026 (Ended Jan 31, 2026)", round(metrics["operating_income"] / 1e6), "USD millions", "Item 8 - Consolidated Statement of Income", ["Operating income"], "29,825"),
        ("net_income_attributable_to_walmart", "FY2026 (Ended Jan 31, 2026)", round(metrics["net_income"] / 1e6), "USD millions", "Item 8 - Consolidated Statement of Income", ["Consolidated net income attributable to Walmart"], "21,893"),
        ("total_assets", "As of Jan 31, 2026", round(metrics["total_assets"] / 1e6), "USD millions", "Item 8 - Consolidated Balance Sheets", ["Total assets"], "284,668"),
        ("current_assets", "As of Jan 31, 2026", round(metrics["current_assets"] / 1e6), "USD millions", "Item 8 - Consolidated Balance Sheets", ["Total current assets"], "84,874"),
        ("current_liabilities", "As of Jan 31, 2026", round(metrics["current_liabilities"] / 1e6), "USD millions", "Item 8 - Consolidated Balance Sheets", ["Total current liabilities"], "107,469"),
        ("walmart_shareholders_equity", "As of Jan 31, 2026", round(metrics["stockholders_equity"] / 1e6), "USD millions", "Item 8 - Consolidated Balance Sheets", ["Total Walmart shareholders", "equity"], "99,617"),
        ("operating_cash_flow", "FY2026 (Ended Jan 31, 2026)", round(metrics["operating_cash_flow"] / 1e6), "USD millions", "Item 8 - Consolidated Statement of Cash Flows", ["Net cash provided by operating activities"], "41,565"),
        ("capital_expenditures", "FY2026 (Ended Jan 31, 2026)", round(metrics["capital_expenditures"] / 1e6), "USD millions", "Item 8 - Consolidated Statement of Cash Flows", ["Payments for property and equipment"], "26,642"),
        ("weighted_avg_shares_basic", "FY2026 (Ended Jan 31, 2026)", round(metrics["shares_outstanding_basic"] / 1e6), "shares (millions)", "Item 8 - Consolidated Statement of Income", ["Basic"], "7,983"),
        ("weighted_avg_shares_diluted", "FY2026 (Ended Jan 31, 2026)", round(metrics["shares_outstanding_diluted"] / 1e6), "shares (millions)", "Item 8 - Consolidated Statement of Income", ["Diluted"], "8,022"),
    ]

    extracted_fields: dict[str, ExtractedField] = {}
    for metric_name, period, val, unit, section, keywords, token in specs:
        snippet = find_verbatim_row(rows, keywords, expected_token=token)
        display_val = f"{val:,}"
        extracted_fields[metric_name] = build_grounded_field(
            metric=metric_name,
            period=period,
            extracted_value=val,
            display_value=display_val,
            unit=unit,
            source_section=section,
            source_snippet=snippet,
        )

    # Also ground the Rule 10b5-1 share count from the URL's targeted Item 9B section.
    plan_snippet = _extract_snippet_around_text(item_9b_text, "Chris Nicholas", radius=320)
    extracted_fields["item_9b_rule_10b5_1_shares"] = build_grounded_field(
        metric="item_9b_rule_10b5_1_shares",
        period="Plan dated Dec 24, 2025",
        extracted_value=34800,
        display_value="34,800",
        unit="shares",
        source_section="Item 9B - Other Information (#i6e16e33efce1499a82f13fd9f63b74c6_199)",
        source_snippet=plan_snippet,
    )

    # Gate all downstream math on Source Grounding Check passing first.
    derived_metrics = [
        safe_compute_derived(
            metric="free_cash_flow",
            formula="operating_cash_flow - capital_expenditures",
            unit="USD millions",
            inputs={
                "operating_cash_flow": extracted_fields["operating_cash_flow"],
                "capital_expenditures": extracted_fields["capital_expenditures"],
            },
            compute_fn=lambda d: round(d["operating_cash_flow"] - d["capital_expenditures"], 2),
        ),
        safe_compute_derived(
            metric="operating_margin_pct",
            formula="(operating_income / total_revenues) * 100",
            unit="%",
            inputs={
                "operating_income": extracted_fields["operating_income"],
                "total_revenues": extracted_fields["total_revenues"],
            },
            compute_fn=lambda d: round((d["operating_income"] / d["total_revenues"]) * 100.0, 2),
        ),
        safe_compute_derived(
            metric="net_margin_pct",
            formula="(net_income_attributable_to_walmart / total_revenues) * 100",
            unit="%",
            inputs={
                "net_income_attributable_to_walmart": extracted_fields["net_income_attributable_to_walmart"],
                "total_revenues": extracted_fields["total_revenues"],
            },
            compute_fn=lambda d: round((d["net_income_attributable_to_walmart"] / d["total_revenues"]) * 100.0, 2),
        ),
        safe_compute_derived(
            metric="current_ratio",
            formula="current_assets / current_liabilities",
            unit="ratio",
            inputs={
                "current_assets": extracted_fields["current_assets"],
                "current_liabilities": extracted_fields["current_liabilities"],
            },
            compute_fn=lambda d: round(d["current_assets"] / d["current_liabilities"], 4),
        ),
    ]

    fields_list = [asdict(f) for f in extracted_fields.values()]
    passed_count = sum(1 for f in extracted_fields.values() if f.grounding.passed)
    failed_count = len(extracted_fields) - passed_count

    payload: dict[str, Any] = {
        "metadata": {
            "company": str(filing.company),
            "cik": filing.cik,
            "form": filing.form,
            "filing_date": str(filing.filing_date),
            "period_of_report": str(tenk.period_of_report),
            "accession_no": filing.accession_no,
            "sec_url": filing.filing_url,
            "extracted_at_utc": datetime.now(timezone.utc).isoformat(),
            "verification_rule": "Source Grounding Check (verbatim digit match in cited source_snippet before math)",
        },
        "summary": {
            "total_fields_checked": len(fields_list),
            "grounded_passed": passed_count,
            "hallucination_flagged": failed_count,
            "all_grounded": failed_count == 0,
        },
        "extracted_fields": fields_list,
        "derived_metrics": [asdict(d) for d in derived_metrics],
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return payload


if __name__ == "__main__":
    out_file = Path(__file__).resolve().parent.parent / "results" / "extracted_wmt_2026_grounded.json"
    result = extract_grounded_walmart_10k(output_path=out_file)
    print(json.dumps(result["summary"], indent=2))

