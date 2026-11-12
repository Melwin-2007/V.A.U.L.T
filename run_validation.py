"""
CLI runner for V.A.U.L.T Balance Sheet Accounting Equation & AI Validation Model.

Examples:
    python run_validation.py --list-ciks
    python run_validation.py --train
    python run_validation.py --cik 0000320193
    python run_validation.py --cik 320193 --inject-apple-error
    python run_validation.py --all
    python run_validation.py --cik 1018724 --json
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

from models.confidence_ai import BalanceSheetValidationAI


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate SEC Balance Sheets using ONLY a CIK with the 7-Step Accounting Engine & AI Model."
    )
    parser.add_argument("--cik", type=str, help="Central Index Key (e.g. 320193 or 0000320193)")
    parser.add_argument("--form", type=str, default=None, help="Optional form preference (10-K or 10-Q)")
    parser.add_argument("--all", action="store_true", help="Validate all CIKs available in the repository")
    parser.add_argument("--list-ciks", action="store_true", help="List all CIKs available offline in the repo")
    parser.add_argument("--train", action="store_true", help="Train/retrain the AI confidence model")
    parser.add_argument(
        "--inject-apple-error",
        action="store_true",
        help="Inject the deliberate Apple FY2023 Liabilities=302,083M wrong-column error for live demo",
    )
    parser.add_argument("--allow-network", action="store_true", help="Fallback to SEC EDGAR API if CIK not in repo")
    parser.add_argument("--log-audit", action="store_true", help="Append validation decision to audit_log.jsonl")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of formatted text")
    args = parser.parse_args()

    ai = BalanceSheetValidationAI()

    if args.train:
        metrics = ai.train(save=True)
        print(json.dumps(metrics, indent=2))
        if not args.cik and not args.all and not args.list_ciks:
            return 0

    if args.list_ciks:
        ciks = ai.loader.list_available_ciks()
        if args.json:
            print(json.dumps(ciks, indent=2))
        else:
            print(f"Available CIKs in repository ({len(ciks)} companies):")
            for item in ciks:
                forms = ", ".join(sorted({f["form"] for f in item["filings"]}))
                print(f"  {item['cik']}  |  {item['ticker']:6s}  |  {item['company_name']} [{forms}]")
        return 0

    if args.all:
        ciks = ai.loader.list_available_ciks()
        reports = []
        for item in ciks:
            rep = ai.validate_cik(item["cik"], log_audit=args.log_audit)
            reports.append(rep)
        if args.json:
            print(json.dumps([r.to_dict() for r in reports], indent=2))
        else:
            print(
                f"{'CIK':10s} | {'Ticker':6s} | {'Company':32s} | {'Form':6s} | {'Period':10s} | "
                f"{'Verdict':7s} | {'AI Conf':8s} | {'Formula':7s} | Reason"
            )
            print("-" * 135)
            for r in reports:
                cur = r.validation_result.current_period
                print(
                    f"{(r.cik or ''):10s} | {(r.ticker or ''):6s} | {r.company_name[:32]:32s} | "
                    f"{r.form[:6]:6s} | {cur.period_label:10s} | {r.verdict.value:7s} | "
                    f"{r.model_confidence * 100:6.2f}% | {cur.effective_equity_formula:7s} | {r.reason[:55]}"
                )
        return 0

    if args.cik:
        if args.inject_apple_error:
            ds = ai.loader.load_by_cik(args.cik, prefer_form=args.form or "10-K", allow_network=args.allow_network)
            corrupted = copy.deepcopy(ds.current_period)
            if ds.prior_period and ds.prior_period.liabilities is not None:
                corrupted.liabilities = ds.prior_period.liabilities
            else:
                corrupted.liabilities = 302_083_000_000.0
            report = ai.validate_dataset(ds, log_audit=args.log_audit, override_current=corrupted)
        else:
            report = ai.validate_cik(
                args.cik,
                prefer_form=args.form,
                allow_network=args.allow_network,
                log_audit=args.log_audit,
            )

        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(report.format_report())
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

