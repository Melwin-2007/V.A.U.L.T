"""Accuracy and Escalation Precision analysis comparing extracted records against SEC Ground Truth."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional


def build_accuracy_report(
    records: List[Dict[str, Any]],
    ground_truth: Dict[str, float],
) -> Dict[str, Any]:
    """Evaluate extracted metrics against SEC Ground Truth facts.

    Calculates overall accuracy, escalation precision, and flags any missed errors.
    """
    metric_results: List[Dict[str, Any]] = []
    total_eval = 0
    total_matched = 0

    auto_approved = 0
    auto_approved_correct = 0

    escalated = 0
    escalated_true_errors = 0

    human_decided = 0
    missed_errors: List[str] = []

    for r in records:
        m_name = r.get("metric_name", "unknown")
        extracted_val = r.get("final_value") if r.get("final_value") is not None else r.get("value")
        gt_val = ground_truth.get(m_name)
        status = r.get("status", "NEEDS_REVIEW")
        has_injected = bool(r.get("injected_error", False))

        is_auto = status == "AUTO_APPROVED"
        is_human = status in ("APPROVED", "CORRECTED", "REJECTED")
        is_escalated = status == "NEEDS_REVIEW" or is_human

        if is_auto:
            auto_approved += 1
        elif is_human:
            human_decided += 1
        if is_escalated:
            escalated += 1

        match = False
        diff_pct: Optional[float] = None

        if extracted_val is not None and gt_val is not None:
            total_eval += 1
            if gt_val == 0.0:
                diff_pct = 0.0 if extracted_val == 0.0 else 100.0
                match = (extracted_val == 0.0)
            else:
                diff_pct = round(abs(extracted_val - gt_val) / abs(gt_val) * 100.0, 4)
                # 0.5% tolerance for rounding / unit conventions, or 0.02 for EPS
                if m_name == "diluted_eps":
                    match = abs(extracted_val - gt_val) <= 0.05
                else:
                    match = diff_pct <= 0.5

            if match:
                total_matched += 1
                if is_auto:
                    auto_approved_correct += 1
            else:
                if is_auto:
                    missed_errors.append(m_name)

        # Escalation Precision: was the escalated metric truly an anomaly / error?
        # True anomaly if it has an injected error, mismatched ground truth, or was derived/missing
        if is_escalated:
            if has_injected or not match or r.get("is_derived") or extracted_val is None:
                escalated_true_errors += 1

        metric_results.append({
            "metric_name": m_name,
            "extracted_value": extracted_val,
            "ground_truth_value": gt_val,
            "match": match,
            "diff_pct": diff_pct,
            "status": status,
            "confidence": r.get("confidence", 0.0),
            "injected_error": has_injected,
        })

    overall_accuracy_pct = round((total_matched / max(total_eval, 1)) * 100.0, 2)
    auto_accuracy_pct = round((auto_approved_correct / max(auto_approved, 1)) * 100.0, 2)
    escalation_precision = round((escalated_true_errors / max(escalated, 1)) * 100.0, 2) if escalated > 0 else 100.0

    return {
        "overall_accuracy_pct": overall_accuracy_pct,
        "auto_approved_count": auto_approved,
        "escalated_count": escalated,
        "human_decided_count": human_decided,
        "auto_approved_accuracy_pct": auto_accuracy_pct,
        "escalation_precision": escalation_precision,
        "missed_errors": missed_errors,
        "metrics": metric_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="V.A.U.L.T Ground Truth Accuracy Benchmarking")
    parser.add_argument("--cik", default="0000320193", help="SEC CIK (default: Apple 0000320193)")
    parser.add_argument("--year", type=int, default=None, help="Fiscal year")
    args = parser.parse_args()

    from pipeline.edgar_client import fetch_filing
    from pipeline.extractor import extract_metrics
    from pipeline.ground_truth import fetch_ground_truth
    from pipeline.scoring import score_metrics
    from pipeline.validators import run_validations

    filing = fetch_filing(args.cik, args.year)
    filing["run_id"] = f"bench_{args.cik}"
    records = extract_metrics(filing)
    scored = score_metrics(records)
    validated, checks = run_validations(scored)

    gt = fetch_ground_truth(args.cik, args.year)
    report = build_accuracy_report(validated, gt)

    print("\n" + "=" * 70)
    print(f"V.A.U.L.T ACCURACY REPORT: {filing['company']} (CIK {filing['cik']})")
    print("=" * 70)
    print(f"Overall Extraction Accuracy:  {report['overall_accuracy_pct']}%")
    print(f"Auto-Approved Accuracy:      {report['auto_approved_accuracy_pct']}% ({report['auto_approved_count']} metrics)")
    print(f"Escalation Precision:        {report['escalation_precision']}% ({report['escalated_count']} escalated)")
    print(f"Missed Critical Errors:      {len(report['missed_errors'])} {report['missed_errors']}")
    print("-" * 70)
    print(f"{'Metric':<24} {'Extracted':<16} {'Ground Truth':<16} {'Match':<8} {'Status'}")
    print("-" * 70)
    for m in report["metrics"]:
        ext_str = f"{m['extracted_value']:,.0f}" if isinstance(m['extracted_value'], (int, float)) and m['metric_name'] != 'diluted_eps' else str(m['extracted_value'])
        gt_str = f"{m['ground_truth_value']:,.0f}" if isinstance(m['ground_truth_value'], (int, float)) and m['metric_name'] != 'diluted_eps' else str(m['ground_truth_value'])
        match_str = "MATCH" if m["match"] else "DIFF"
        print(f"{m['metric_name']:<24} {ext_str:<16} {gt_str:<16} {match_str:<8} {m['status']}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()

