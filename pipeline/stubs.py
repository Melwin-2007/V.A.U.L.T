"""Pipeline stub functions providing realistic mock data per docs/CONTRACT.md.

Allows Member B (Agent Brain) and Member C (Dashboard) to build and test
without waiting for Member A's live extraction code.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def fetch_filing(
    cik: str,
    fiscal_year: int = 2023,
    *,
    allow_network: bool = True,
) -> Dict[str, Any]:
    """Stub returning realistic SEC filing metadata for Apple (CIK 0000320193)."""
    normalized_cik = str(cik).strip().zfill(10)
    return {
        "cik": normalized_cik,
        "company": "Apple Inc.",
        "form": "10-K",
        "fiscal_year": fiscal_year,
        "period_end": f"{fiscal_year}-09-30",
        "filing_date": f"{fiscal_year}-11-03",
        "accession_number": f"{normalized_cik}-23-000106",
        "source": "offline_fixture",
        "source_url": f"https://www.sec.gov/ix?doc=/Archives/edgar/data/{normalized_cik}/000032019323000106/aapl-{fiscal_year}0930.htm",
        "html_path": "edgartools/tests/fixtures/xbrl/aapl/10k_2023/aapl-20230930.htm",
    }


def extract_metrics(filing: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Stub returning 10 standardized Metric Records for Apple FY2023.

    Includes one deliberate anomaly: shareholders_equity extracted as 72,146M
    instead of 62,146M (to test confidence drop and accounting equation escalation).
    """
    cik = filing.get("cik", "0000320193")
    company = filing.get("company", "Apple Inc.")
    year = filing.get("fiscal_year", 2023)
    run_id = f"run_{year}_{cik[-6:]}"
    url = filing.get("source_url", "")

    return [
        {
            "metric_id": f"{run_id}:revenue",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "revenue",
            "value": 383_285_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
            "source_snippet": "Total net sales: $383,285 million for fiscal year 2023.",
            "source_url": url,
            "confidence": 1.0,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 1.0},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 383_285_000_000.0,
        },
        {
            "metric_id": f"{run_id}:net_income",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "net_income",
            "value": 96_995_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:NetIncomeLoss",
            "source_snippet": "Net income: $96,995 million.",
            "source_url": url,
            "confidence": 1.0,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 1.0},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 96_995_000_000.0,
        },
        {
            "metric_id": f"{run_id}:total_assets",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "total_assets",
            "value": 352_583_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:Assets",
            "source_snippet": "Total assets: $352,583 million as of September 30, 2023.",
            "source_url": url,
            "confidence": 1.0,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 1.0},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 352_583_000_000.0,
        },
        {
            "metric_id": f"{run_id}:total_liabilities",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "total_liabilities",
            "value": 290_437_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:Liabilities",
            "source_snippet": "Total liabilities: $290,437 million as of September 30, 2023.",
            "source_url": url,
            "confidence": 1.0,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 1.0},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 290_437_000_000.0,
        },
        {
            # Deliberate injection: 72,146M vs ground truth 62,146M!
            "metric_id": f"{run_id}:shareholders_equity",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "shareholders_equity",
            "value": 72_146_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:StockholdersEquity",
            "source_snippet": "Total shareholders' equity: $62,146 million as of September 30, 2023.",
            "source_url": url,
            "confidence": 0.52,
            "confidence_breakdown": {"grounding": 0.0, "label": 1.0, "unit": 1.0, "yoy": 0.3},
            "is_derived": False,
            "flags": [],
            "status": "NEEDS_REVIEW",
            "escalation_reason": "Low Confidence (0.52 < 0.85): Digits '72,146' not found verbatim in source snippet.",
            "final_value": None,
        },
        {
            "metric_id": f"{run_id}:operating_cash_flow",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "operating_cash_flow",
            "value": 110_543_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:NetCashProvidedByUsedInOperatingActivities",
            "source_snippet": "Cash generated by operating activities: $110,543 million.",
            "source_url": url,
            "confidence": 0.98,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 0.9},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 110_543_000_000.0,
        },
        {
            "metric_id": f"{run_id}:current_assets",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "current_assets",
            "value": 143_566_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:AssetsCurrent",
            "source_snippet": "Total current assets: $143,566 million.",
            "source_url": url,
            "confidence": 0.98,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 0.9},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 143_566_000_000.0,
        },
        {
            "metric_id": f"{run_id}:current_liabilities",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "current_liabilities",
            "value": 145_308_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:LiabilitiesCurrent",
            "source_snippet": "Total current liabilities: $145,308 million.",
            "source_url": url,
            "confidence": 0.98,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 0.9},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 145_308_000_000.0,
        },
        {
            "metric_id": f"{run_id}:cash_and_equivalents",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "cash_and_equivalents",
            "value": 29_965_000_000.0,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:CashAndCashEquivalentsAtCarryingValue",
            "source_snippet": "Cash and cash equivalents: $29,965 million.",
            "source_url": url,
            "confidence": 1.0,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 1.0},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 29_965_000_000.0,
        },
        {
            "metric_id": f"{run_id}:diluted_eps",
            "run_id": run_id,
            "cik": cik,
            "company": company,
            "fiscal_year": year,
            "metric_name": "diluted_eps",
            "value": 6.13,
            "unit": "USD",
            "period_start": f"{year-1}-10-01",
            "period_end": f"{year}-09-30",
            "xbrl_tag": "us-gaap:EarningsPerShareDiluted",
            "source_snippet": "Diluted earnings per share: $6.13.",
            "source_url": url,
            "confidence": 0.98,
            "confidence_breakdown": {"grounding": 1.0, "label": 1.0, "unit": 1.0, "yoy": 0.9},
            "is_derived": False,
            "flags": [],
            "status": "AUTO_APPROVED",
            "escalation_reason": None,
            "final_value": 6.13,
        },
    ]


def score_metrics(
    records: List[Dict[str, Any]],
    *,
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Stub applying the 4-factor confidence score to each record."""
    threshold = float((config or {}).get("confidence_threshold", 0.85))
    scored = []
    for r in records:
        rec = dict(r)
        bd = rec.get("confidence_breakdown", {})
        g = float(bd.get("grounding", 1.0))
        l = float(bd.get("label", 1.0))
        u = float(bd.get("unit", 1.0))
        y = float(bd.get("yoy", 1.0))
        score = round((0.40 * g) + (0.25 * l) + (0.20 * u) + (0.15 * y), 4)
        rec["confidence"] = score
        if score < threshold:
            rec["status"] = "NEEDS_REVIEW"
            rec["escalation_reason"] = (
                f"Low Confidence ({score:.2f} < {threshold:.2f}): "
                + ("Digits missing from source snippet." if g == 0.0 else "Check row labels / scaling.")
            )
        scored.append(rec)
    return scored


def run_validations(
    records: List[Dict[str, Any]],
    history: Optional[List[Dict[str, Any]]] = None,
    *,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Stub evaluating accounting equation, segments, and YoY consistency.

    Produces a hard check failure for Apple balance sheet because
    equity (72,146M) + liabilities (290,437M) = 362,583M != assets (352,583M).
    """
    by_name = {r["metric_name"]: r for r in records}
    checks: List[Dict[str, Any]] = []

    # 1. Accounting Equation Check
    a = by_name.get("total_assets", {}).get("value", 0.0)
    l = by_name.get("total_liabilities", {}).get("value", 0.0)
    e = by_name.get("shareholders_equity", {}).get("value", 0.0)

    acct_diff = round(a - (l + e), 2)
    acct_passed = abs(acct_diff) <= 2_000_000.0  # $2M rounding tolerance

    check_acct = {
        "check_name": "accounting_equation",
        "passed": acct_passed,
        "severity": "hard",
        "affected_metrics": ["total_assets", "total_liabilities", "shareholders_equity"],
        "message": (
            f"Balance Sheet reconciles: Assets ({a/1e6:,.0f}M) == Liabilities ({l/1e6:,.0f}M) + Equity ({e/1e6:,.0f}M)"
            if acct_passed
            else f"Accounting Equation Failed: Assets ({a/1e6:,.0f}M) != Liabilities ({l/1e6:,.0f}M) + Equity ({e/1e6:,.0f}M), diff={acct_diff/1e6:,.0f}M"
        ),
    }
    checks.append(check_acct)

    # 2. Segment Reconciliation Check (Passes for Apple)
    check_seg = {
        "check_name": "segment_reconciliation",
        "passed": True,
        "severity": "hard",
        "affected_metrics": ["revenue"],
        "message": "ASC 280 segments (Americas, Europe, China, Japan, APAC) reconcile exactly to consolidated revenue ($383,285M).",
    }
    checks.append(check_seg)

    # 3. Source Grounding Check
    eq_rec = by_name.get("shareholders_equity", {})
    grounding_passed = eq_rec.get("confidence_breakdown", {}).get("grounding", 1.0) == 1.0
    check_ground = {
        "check_name": "source_grounding",
        "passed": grounding_passed,
        "severity": "hard",
        "affected_metrics": ["shareholders_equity"],
        "message": (
            "Source grounding verified for all metrics."
            if grounding_passed
            else "Source Grounding Failed: Exact digits '72,146' not found in source snippet."
        ),
    }
    checks.append(check_ground)

    # Trigger B: Force NEEDS_REVIEW across all affected metrics if a hard check fails
    updated_records = []
    for r in records:
        rec = dict(r)
        m_name = rec["metric_name"]
        rec_flags = [c for c in checks if m_name in c["affected_metrics"]]
        rec["flags"] = rec_flags

        failed_hard = [c for c in rec_flags if (not c["passed"]) and c["severity"] == "hard"]
        if failed_hard:
            rec["status"] = "NEEDS_REVIEW"
            reasons = [c["message"] for c in failed_hard]
            rec["escalation_reason"] = "; ".join(reasons)
        updated_records.append(rec)

    return updated_records, checks


def fetch_ground_truth(cik: str, fiscal_year: int = 2023) -> Dict[str, float]:
    """Stub returning official SEC Company Facts values for Apple FY2023."""
    return {
        "revenue": 383_285_000_000.0,
        "net_income": 96_995_000_000.0,
        "total_assets": 352_583_000_000.0,
        "total_liabilities": 290_437_000_000.0,
        "shareholders_equity": 62_146_000_000.0,  # Official value is 62,146M
        "operating_cash_flow": 110_543_000_000.0,
        "current_assets": 143_566_000_000.0,
        "current_liabilities": 145_308_000_000.0,
        "cash_and_equivalents": 29_965_000_000.0,
        "diluted_eps": 6.13,
    }


def build_accuracy_report(
    records: List[Dict[str, Any]],
    ground_truth: Dict[str, float],
) -> Dict[str, Any]:
    """Stub computing accuracy and escalation precision against SEC ground truth."""
    total = len(records)
    matches = 0
    metric_results = []
    auto_approved_count = 0
    escalated_count = 0
    true_anomalies_escalated = 0
    missed_errors = []

    for r in records:
        m_name = r["metric_name"]
        extracted_val = r["value"]
        gt_val = ground_truth.get(m_name)

        if gt_val is not None:
            diff_pct = abs(extracted_val - gt_val) / abs(gt_val) if gt_val != 0 else 0.0
            is_match = diff_pct <= 0.005  # 0.5% rounding tolerance
        else:
            is_match = True

        if is_match:
            matches += 1
        elif r["status"] == "AUTO_APPROVED":
            missed_errors.append(
                {
                    "metric_name": m_name,
                    "extracted_value": extracted_val,
                    "ground_truth": gt_val,
                    "reason": "Auto-approved despite mismatch with SEC Company Facts.",
                }
            )

        if r["status"] == "AUTO_APPROVED":
            auto_approved_count += 1
        else:
            escalated_count += 1
            if not is_match:
                true_anomalies_escalated += 1

        metric_results.append(
            {
                "metric_name": m_name,
                "extracted_value": extracted_val,
                "ground_truth_value": gt_val,
                "match": is_match,
                "status": r["status"],
                "confidence": r["confidence"],
            }
        )

    accuracy_pct = round((matches / total) * 100.0, 2) if total > 0 else 100.0
    precision_pct = (
        round((true_anomalies_escalated / escalated_count) * 100.0, 2)
        if escalated_count > 0
        else 100.0
    )

    return {
        "cik": records[0]["cik"] if records else "0000320193",
        "company": records[0]["company"] if records else "Apple Inc.",
        "fiscal_year": records[0]["fiscal_year"] if records else 2023,
        "total_metrics": total,
        "matched_metrics": matches,
        "overall_accuracy_pct": accuracy_pct,
        "auto_approved_count": auto_approved_count,
        "escalated_count": escalated_count,
        "escalation_precision_pct": precision_pct,
        "missed_errors": missed_errors,
        "metric_breakdown": metric_results,
    }
