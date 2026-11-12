"""Windows PowerShell / terminal CLI for the Year-on-Year Financial Validation Agent."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Optional

from app.models.yoy_models import (
    HumanAction,
    HumanReviewRequest,
    ValidationResult,
    YearMetricInput,
    YoYValidationRequest,
    YoYValidationResponse,
)
from app.services.audit_logger import AuditStore
from app.services.sec_html_extractor import (
    extract_yoy_requests_from_html,
    fetch_filing_html,
)
from app.services.yoy_validator import apply_human_review, validate_yoy

DEFAULT_AUDIT_LOG = Path("audit_logs/yoy_audit_trail.jsonl")


def _print_single_result(resp: YoYValidationResponse) -> None:
    yoy_str = (
        "N/A"
        if resp.yoy_percent is None
        else f"{'+' if resp.yoy_percent > 0 else ''}{resp.yoy_percent}%"
    )
    print("=" * 72)
    print(f"Company:               {resp.company or '-'}")
    print(f"Metric:                {resp.metric or '-'}")
    print(
        f"Previous Year:         {resp.previous_value} {resp.unit or ''} ({resp.previous_year})"
    )
    print(
        f"Current Year:          {resp.current_value} {resp.unit or ''} ({resp.current_year})"
    )
    print(f"YoY Change:            {yoy_str}")
    print(f"Configured Threshold:  {resp.threshold_percent}%")
    print(f"Validation Status:     {resp.validation_result.value}")
    print(f"Human Review Required: {'Yes' if resp.human_review_required else 'No'}")
    print(f"Automated Confidence:  {resp.automated_confidence}")
    print(f"Final Status:          {resp.final_status.value}")
    print(f"Reason:                {resp.reason}")
    print(f"Validation ID:         {resp.validation_id}")
    print("=" * 72)


def _prompt_human_review(
    resp: YoYValidationResponse, store: AuditStore
) -> YoYValidationResponse:
    if resp.validation_result == ValidationResult.CANNOT_VALIDATE:
        return resp
    choice = input(
        "\nHuman review [APPROVE / CORRECT / REJECT / skip]: "
    ).strip().upper()
    if not choice or choice == "SKIP":
        return resp
    if choice not in {"APPROVE", "CORRECT", "REJECT"}:
        print(f"Unknown action {choice!r}; skipping review.")
        return resp

    corrected_val: Optional[str] = None
    if choice == "CORRECT":
        corrected_val = input("Enter corrected current-year value: ").strip()
    reviewer = input("Reviewer name (optional): ").strip() or None
    notes = input("Review notes (optional): ").strip() or None

    review_req = HumanReviewRequest(
        validation_id=resp.validation_id,
        action=HumanAction(choice),
        corrected_value=corrected_val,
        reviewer=reviewer,
        notes=notes,
    )
    updated = apply_human_review(resp, review_req)
    store.record_review(updated, review_req)
    print(
        f"Recorded {updated.human_decision.value} -> final_status={updated.final_status.value}, "
        f"original_value={updated.original_value}, corrected_value={updated.corrected_value}, "
        f"automated_confidence={updated.automated_confidence}"
    )
    return updated


def _render_markdown_report(
    results: list[YoYValidationResponse], source_label: str
) -> str:
    passed = [r for r in results if r.validation_result == ValidationResult.PASS]
    unusual = [r for r in results if r.validation_result == ValidationResult.UNUSUAL]
    cannot = [
        r for r in results if r.validation_result == ValidationResult.CANNOT_VALIDATE
    ]

    lines = [
        "# Year-on-Year Financial Validation Report",
        "",
        f"**Source:** `{source_label}`  ",
        f"**Total Metrics:** {len(results)} | **PASS:** {len(passed)} | "
        f"**UNUSUAL:** {len(unusual)} | **CANNOT_VALIDATE:** {len(cannot)}",
        "",
        "## Flagged Unusual Changes (Human Review Required)",
        "",
    ]
    if not unusual:
        lines.append("None.")
    else:
        lines.append("| Metric | Prev | Curr | Unit | YoY % | Threshold | Reason |")
        lines.append("| :--- | ---: | ---: | :--- | ---: | ---: | :--- |")
        for r in unusual:
            lines.append(
                f"| {r.metric} | {r.previous_value} ({r.previous_year}) | "
                f"{r.current_value} ({r.current_year}) | {r.unit} | "
                f"{r.yoy_percent}% | {r.threshold_percent}% | {r.reason} |"
            )

    lines.extend(["", "## All Validated Metrics", ""])
    lines.append("| Metric | Prev | Curr | Unit | YoY % | Validation | Final Status |")
    lines.append("| :--- | ---: | ---: | :--- | ---: | :--- | :--- |")
    for r in results:
        yoy_txt = "N/A" if r.yoy_percent is None else f"{r.yoy_percent}%"
        lines.append(
            f"| {r.metric} | {r.previous_value} ({r.previous_year}) | "
            f"{r.current_value} ({r.current_year}) | {r.unit or '-'} | "
            f"{yoy_txt} | {r.validation_result.value} | {r.final_status.value} |"
        )
    lines.append("")
    return "\n".join(lines)


def _print_batch_summary(results: list[YoYValidationResponse]) -> None:
    passed = sum(1 for r in results if r.validation_result == ValidationResult.PASS)
    unusual = [r for r in results if r.validation_result == ValidationResult.UNUSUAL]
    cannot = [
        r for r in results if r.validation_result == ValidationResult.CANNOT_VALIDATE
    ]

    print(
        f"\nEvaluated {len(results)} metrics -> PASS: {passed}, "
        f"UNUSUAL: {len(unusual)}, CANNOT_VALIDATE: {len(cannot)}\n"
    )
    print(f"{'VALIDATION':16} | {'YOY %':>11} | {'UNIT':15} | METRIC")
    print("-" * 78)
    for r in results:
        yoy_txt = "N/A" if r.yoy_percent is None else f"{r.yoy_percent}%"
        print(
            f"{r.validation_result.value:16} | {yoy_txt:>11} | "
            f"{(r.unit or '-'):15} | {r.metric}"
        )


def _interactive_prompt(threshold_default: str) -> YoYValidationRequest:
    print("Year-on-Year Financial Validation Agent (Interactive CLI)")
    print("-" * 56)
    company = input("Company [Apple Inc.]: ").strip() or "Apple Inc."
    metric = input("Metric [Revenue]: ").strip() or "Revenue"
    prev_year_s = input("Previous Year [2024]: ").strip() or "2024"
    prev_val_s = input("Previous Value [100]: ").strip() or "100"
    prev_unit = input("Previous Unit [USD millions]: ").strip() or "USD millions"
    curr_year_s = input("Current Year [2025]: ").strip() or "2025"
    curr_val_s = input("Current Value [145]: ").strip() or "145"
    curr_unit = input("Current Unit [USD millions]: ").strip() or "USD millions"
    threshold_s = (
        input(f"Threshold % [{threshold_default}]: ").strip() or threshold_default
    )
    conf_s = input("Automated Confidence 0-1 (optional): ").strip()

    return YoYValidationRequest(
        company=company,
        metric=metric,
        previous_year=YearMetricInput(
            year=int(prev_year_s) if prev_year_s else None,
            value=prev_val_s or None,
            unit=prev_unit or None,
        ),
        current_year=YearMetricInput(
            year=int(curr_year_s) if curr_year_s else None,
            value=curr_val_s or None,
            unit=curr_unit or None,
        ),
        threshold_percent=threshold_s,
        automated_confidence=float(conf_s) if conf_s else None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Local PowerShell/Terminal CLI for the YoY Financial Validation Agent.",
    )
    parser.add_argument("--url", help="SEC 10-K HTML URL to extract and validate.")
    parser.add_argument("--html-file", help="Local SEC 10-K HTML file path.")
    parser.add_argument(
        "--file", help="JSON file with a YoYValidationRequest object or list of objects."
    )
    parser.add_argument("--company", help="Company name.")
    parser.add_argument("--metric", help="Financial metric name.")
    parser.add_argument("--prev-year", type=int, help="Previous reporting year.")
    parser.add_argument("--prev-value", help="Previous-year numeric value.")
    parser.add_argument("--prev-unit", help="Previous-year unit (e.g. 'USD millions').")
    parser.add_argument("--curr-year", type=int, help="Current reporting year.")
    parser.add_argument("--curr-value", help="Current-year numeric value.")
    parser.add_argument("--curr-unit", help="Current-year unit (e.g. 'USD millions').")
    parser.add_argument(
        "--threshold",
        default="30",
        help="Configurable YoY threshold percent (default: 30).",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=None,
        help="Optional upstream automated confidence (0.0 to 1.0).",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Prompt for human review (APPROVE/CORRECT/REJECT) on flagged items.",
    )
    parser.add_argument(
        "--report-out", help="Write a Markdown report to this file path."
    )
    parser.add_argument("--json-out", help="Write raw JSON results to this file path.")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the local FastAPI server and Demo UI.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for --serve.")
    parser.add_argument("--port", type=int, default=8001, help="Port for --serve.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.serve:
        import uvicorn

        print(f"Starting Demo UI & API at http://{args.host}:{args.port}/")
        uvicorn.run("app.main:app", host=args.host, port=args.port, reload=False)
        return 0

    store = AuditStore(log_file=DEFAULT_AUDIT_LOG)

    if args.url or args.html_file:
        if args.url:
            print(f"Fetching SEC filing from {args.url} ...")
            raw_html = fetch_filing_html(args.url)
            source = args.url
        else:
            html_path = Path(args.html_file)
            raw_html = html_path.read_text(encoding="utf-8")
            source = str(html_path)

        requests = extract_yoy_requests_from_html(
            raw_html,
            threshold_percent=Decimal(str(args.threshold)),
            company_override=args.company,
        )
        if not requests:
            print("No comparable multi-year financial statement rows were found.")
            return 1

        results = [store.save_validation(validate_yoy(req)) for req in requests]
        _print_batch_summary(results)

        if args.review:
            for idx, r in enumerate(results):
                if r.validation_result == ValidationResult.UNUSUAL:
                    _print_single_result(r)
                    results[idx] = _prompt_human_review(r, store)

        if args.report_out:
            Path(args.report_out).write_text(
                _render_markdown_report(results, source), encoding="utf-8"
            )
            print(f"\nSaved Markdown report to {args.report_out}")
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps([r.model_dump(mode="json") for r in results], indent=2),
                encoding="utf-8",
            )
            print(f"Saved JSON results to {args.json_out}")
        return 0

    if args.file:
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        items = payload if isinstance(payload, list) else [payload]
        results = []
        for item in items:
            req = YoYValidationRequest(**item)
            res = store.save_validation(validate_yoy(req))
            results.append(res)
            _print_single_result(res)
            if args.review and res.validation_result == ValidationResult.UNUSUAL:
                _prompt_human_review(res, store)
        if args.json_out:
            Path(args.json_out).write_text(
                json.dumps([r.model_dump(mode="json") for r in results], indent=2),
                encoding="utf-8",
            )
        return 0

    has_cli_values = any(
        v is not None
        for v in (
            args.company,
            args.metric,
            args.prev_year,
            args.prev_value,
            args.prev_unit,
            args.curr_year,
            args.curr_value,
            args.curr_unit,
        )
    )
    if has_cli_values:
        req = YoYValidationRequest(
            company=args.company,
            metric=args.metric,
            previous_year=YearMetricInput(
                year=args.prev_year,
                value=args.prev_value,
                unit=args.prev_unit,
            ),
            current_year=YearMetricInput(
                year=args.curr_year,
                value=args.curr_value,
                unit=args.curr_unit,
            ),
            threshold_percent=args.threshold,
            automated_confidence=args.confidence,
        )
    else:
        req = _interactive_prompt(str(args.threshold))

    resp = store.save_validation(validate_yoy(req))
    _print_single_result(resp)
    if args.review or not has_cli_values:
        _prompt_human_review(resp, store)
    return 0


if __name__ == "__main__":
    sys.exit(main())
