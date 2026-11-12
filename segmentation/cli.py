from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from segmentation.confidence import DEFAULT_CONFIDENCE_THRESHOLD, DEFAULT_YOY_THRESHOLD
from segmentation.models import MetricValidation, SegmentationReport
from segmentation.validator import validate_company_segments, validate_xbrl_directory


def _format_metric_line(mv: MetricValidation) -> str:
    c = mv.confidence
    yoy_str = f"{mv.yoy_change_pct:+.2f}%" if mv.yoy_change_pct is not None else "N/A"
    return (
        f"  - {mv.label} ({mv.metric}): {mv.display_value} {mv.unit} | "
        f"Score={c.score:.2f} [G={c.s_grounding:.1f}, L={c.s_label:.1f}, U={c.s_unit:.1f}, YoY={c.s_yoy:.1f} ({yoy_str})] | "
        f"{mv.decision}"
    )


def format_report_text(report: SegmentationReport) -> str:
    """Format a human-readable CLI report for segment & accounting validation."""
    seg = report.segment_reconciliation
    acct = report.accounting_equation
    lines = [
        f"=== SEGMENTATION & CONFIDENCE VALIDATION: {report.company_name} (CIK {report.cik}) ===",
        f"Form: {report.form} | Period: {report.period_of_report or 'N/A'} | Filing Date: {report.filing_date or 'N/A'}",
        f"Accession: {report.accession_no or 'Offline XBRL'} | Threshold: {report.confidence_threshold:.2f}",
        f"VERDICT: {report.verdict} ({report.review_decision})",
        "",
        "1. Consolidated Revenue",
        _format_metric_line(report.consolidated_revenue),
        "",
        f"2. Segment Validation (ASC 280) [{seg.status}]",
        f"   Breakdown: {seg.breakdown_type}" + (f" ({seg.axis_used})" if seg.axis_used else ""),
    ]

    if seg.status == "N/A - Single Segment":
        lines.append(f"   Detail: {seg.reason}")
    else:
        for item in seg.items:
            lines.append(_format_metric_line(item.validation))
        lines.append(
            f"   Formula: Segments ({seg.segments_sum:,.0f}) + Corporate/Other ({seg.corporate_other:,.0f}) "
            f"- Eliminations ({seg.intersegment_eliminations:,.0f}) = {seg.reconciled_total:,.0f} "
            f"vs Consolidated ({seg.consolidated_total:,.0f})"
        )
        lines.append(f"   Reconciliation: {seg.reason}")

    lines.extend(["", f"3. Balance Sheet Accounting Equation [{'PASS' if acct.passed else 'FAIL'}]"])
    for key in ("total_assets", "total_liabilities", "stockholders_equity"):
        if key in acct.metrics:
            lines.append(_format_metric_line(acct.metrics[key]))
    lines.append(f"   Check: {acct.reason}")

    if report.escalation_reasons:
        lines.extend(["", "Escalation Reasons (Human Review Required):"])
        for reason in report.escalation_reasons:
            lines.append(f"  * {reason}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m segmentation",
        description="Validate SEC 10-K Segment Reporting (ASC 280) and weighted extraction confidence for any CIK.",
    )
    parser.add_argument(
        "cik",
        help="SEC CIK (e.g. 0000320193 or 320193) or ticker symbol (e.g. AAPL).",
    )
    parser.add_argument(
        "--form",
        default="10-K",
        help="SEC form type to inspect (default: 10-K).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_CONFIDENCE_THRESHOLD,
        help=f"Minimum confidence score for auto-approval (default: {DEFAULT_CONFIDENCE_THRESHOLD}).",
    )
    parser.add_argument(
        "--yoy-threshold",
        type=float,
        default=DEFAULT_YOY_THRESHOLD,
        help=f"Maximum normal YoY fractional change before flagging (default: {DEFAULT_YOY_THRESHOLD}).",
    )
    parser.add_argument(
        "--identity",
        default=None,
        help="SEC EDGAR User-Agent identity string (defaults to EDGAR_IDENTITY env var).",
    )
    parser.add_argument(
        "--xbrl-dir",
        default=None,
        help="Optional local XBRL directory for offline validation instead of fetching from SEC.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write full JSON validation report.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON report to stdout instead of summary text.",
    )
    parser.add_argument(
        "--simulate-anomaly",
        choices=["segment-math", "accounting-equation", "hallucination"],
        default=None,
        help="Simulate a specific flaw: 'segment-math' (sum mismatch), 'accounting-equation' (balance sheet typo), or 'hallucination'.",
    )
    parser.add_argument(
        "--strict-exit",
        action="store_true",
        help="Exit with code 2 when VERDICT is FAIL.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.xbrl_dir:
        report = validate_xbrl_directory(
            args.xbrl_dir,
            cik=args.cik,
            confidence_threshold=args.threshold,
            yoy_threshold=args.yoy_threshold,
            simulate_anomaly=args.simulate_anomaly,
        )
    else:
        report = validate_company_segments(
            args.cik,
            form=args.form,
            identity=args.identity,
            confidence_threshold=args.threshold,
            yoy_threshold=args.yoy_threshold,
            simulate_anomaly=args.simulate_anomaly,
        )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(format_report_text(report))

    if args.strict_exit and report.verdict == "FAIL":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

