"""Interactive terminal runner for the Accounting Equation Validation Engine."""

from __future__ import annotations

import argparse
import copy
import json
import random
import sys
from collections import defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from .accounting_equation import _format_scaled_amount, validate_balance_sheet_input
from .adapter import list_available_filings, load_balance_sheet
from .models import BalanceSheetInput, CheckResult, ValidationConfig, load_config


def _fmt_dollars_and_scaled(val: Optional[Decimal], scale: Optional[int]) -> str:
    if val is None:
        return "— (not printed)"
    scaled_str = _format_scaled_amount(val, scale)
    return f"{scaled_str}  (${int(val):,})" if val == val.to_integral_value() else f"{scaled_str}  (${val:,.2f})"


def _catalog_by_cik() -> List[Dict[str, Any]]:
    filings = list_available_filings()
    grouped: Dict[str, Dict[str, Any]] = {}
    for f in filings:
        cik = f["cik"]
        entry = grouped.setdefault(
            cik,
            {
                "cik": cik,
                "company": f["company"],
                "ticker": f["ticker"] or "-",
                "forms": set(),
                "years": set(),
            },
        )
        if f["form"]:
            entry["forms"].add(f["form"])
        if f["fiscal_year"]:
            entry["years"].add(str(f["fiscal_year"]))
        if entry["ticker"] == "-" and f["ticker"]:
            entry["ticker"] = f["ticker"]
    out = sorted(grouped.values(), key=lambda x: x["company"].lower())
    for item in out:
        item["forms"] = ", ".join(sorted(item["forms"])) or "-"
        item["years"] = ", ".join(sorted(item["years"])) or "-"
    return out


def _print_catalog(catalog: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 92)
    print("  V.A.U.L.T. — INTERACTIVE ACCOUNTING EQUATION VALIDATION TEST")
    print("=" * 92)
    print(f"Available SEC Filings in Repository ({len(catalog)} companies, non-CompanyFacts):")
    print(f"  {'CIK':<10} | {'Ticker':<6} | {'Company':<38} | {'Forms':<10} | {'Years'}")
    print("  " + "-" * 86)
    for item in catalog:
        print(
            f"  {item['cik']:<10} | {item['ticker']:<6} | {item['company'][:38]:<38} | "
            f"{item['forms'][:10]:<10} | {item['years']}"
        )
    print("-" * 92)


def _apply_error_simulation(bs: BalanceSheetInput, mode: str) -> Tuple[BalanceSheetInput, str]:
    mutated = copy.deepcopy(bs)
    cur = mutated.current
    if cur is None:
        return mutated, "none (no current period)"

    scale = cur.scale or 1_000_000
    m = mode.strip().lower()

    if m in ("", "none", "0", "n"):
        return mutated, "None (clean unmodified filing values)"

    if m in ("1", "wrong_column", "prior"):
        if cur.L is not None and mutated.prior and mutated.prior.L is not None and cur.L != mutated.prior.L:
            old = cur.L
            cur.L = mutated.prior.L
            return mutated, f"Injected wrong_column on Liabilities: {int(old):,} -> prior-year {int(cur.L):,}"
        if mutated.prior and mutated.prior.E is not None and cur.E is not None:
            old = cur.E
            cur.E = mutated.prior.E
            return mutated, f"Injected wrong_column on Equity: {int(old):,} -> prior-year {int(cur.E):,}"

    if m in ("2", "x1000", "x1000_scale", "scale"):
        if cur.L is not None:
            cur.L = cur.L * Decimal(1000)
            return mutated, "Injected x1000 scale error on Total Liabilities (L * 1,000)"
        if cur.A is not None:
            cur.A = cur.A * Decimal(1000)
            return mutated, "Injected x1000 scale error on Total Assets (A * 1,000)"

    if m in ("3", "sign", "sign_flip", "sign_error"):
        if cur.E is not None:
            cur.E = -cur.E
            return mutated, "Injected sign_error on Equity (E -> -E)"

    if m in ("4", "typo", "assets_typo", "assets"):
        if cur.A is not None:
            delta = Decimal(6417) * Decimal(scale)
            cur.A = cur.A + delta
            return mutated, f"Injected typo on Total Assets (A + {_format_scaled_amount(delta, scale)})"

    if m in ("5", "wrong_row", "row"):
        if cur.L is not None and cur.LC is not None:
            cur.L = cur.LC
            return mutated, "Injected wrong_row on Liabilities (replaced Total Liabilities with Current Liabilities LC)"
        if cur.AC is not None:
            cur.A = cur.AC
            return mutated, "Injected wrong_row on Assets (replaced Total Assets with Current Assets AC)"
        if cur.rows:
            lbl, rval = next(iter(cur.rows.items()))
            cur.A = rval
            return mutated, f"Injected wrong_row on Assets (replaced Total Assets with row '{lbl}')"

    if m in ("6", "transposition", "digit_transposition"):
        if cur.A is not None:
            unscaled = int(cur.A / Decimal(scale))
            digits = list(str(abs(unscaled)))
            swapped = False
            for i in range(len(digits) - 1):
                if digits[i] != digits[i + 1]:
                    digits[i], digits[i + 1] = digits[i + 1], digits[i]
                    swapped = True
                    break
            new_unscaled = int("".join(digits)) if swapped else (abs(unscaled) + 9)
            if unscaled < 0:
                new_unscaled = -new_unscaled
            cur.A = Decimal(new_unscaled) * Decimal(scale)
            return mutated, "Injected digit_transposition on Total Assets (adjacent digits swapped)"

    return mutated, f"Unknown simulation mode {mode!r}; ran unmodified filing"


def _formula_expr(check_id: str, formula: str) -> str:
    return {
        "F4": "A = LE",
        "F1": "A = L + E",
        "F2": "A = L + E + NCI",
        "F3": "A = L + TE + E (+ NCI)",
        "F5": "L + TE + E_total = LE",
        "F6": "L = LE - E_total - TE",
        "F7": "A = AC + ANC",
        "F8": "L = LC + LNC",
    }.get(formula, check_id)


def render_detailed_report(bs: BalanceSheetInput, report: Any, cfg: ValidationConfig, sim_note: str) -> None:
    cur = bs.current
    pri = bs.prior
    scale = (cur.scale if cur and cur.scale else 1_000_000)
    tolerance = Decimal(cfg.tolerance_multiplier) * Decimal(scale)

    print("\n" + "=" * 92)
    print(f"  FILING: {bs.company}  |  CIK: {bs.cik}  |  Form: {bs.form or '-'}  |  FY: {bs.fiscal_year or '-'}")
    print(f"  Accession: {bs.accession or 'N/A'}  |  Source: {bs.source_path}")
    print(f"  Simulation: {sim_note}")
    print("=" * 92)

    # Step 1: Normalized Inputs
    print("\n[STEP 1] NORMALIZATION & TOLERANCE")
    print(f"  Table Scale       : {scale:,} ({ {1: 'exact dollars', 1000: 'in thousands', 1000000: 'in millions', 1000000000: 'in billions'}.get(scale, str(scale)) })")
    print(f"  Tolerance Formula : {cfg.tolerance_multiplier} x {scale:,} = +/- {_format_scaled_amount(tolerance, scale)} (${int(tolerance):,})")
    if cur:
        print(f"  Consolidated?     : {cur.is_consolidated}   |   Equity Basis: {cur.equity_reporting_basis}")

    cur_lbl = cur.period_end if cur else "N/A"
    pri_lbl = pri.period_end if pri else "N/A"
    print(f"\n  {'Metric':<30} | {'Current (' + str(cur_lbl) + ')':<28} | {'Prior (' + str(pri_lbl) + ')'}")
    print("  " + "-" * 88)
    for label, attr in [
        ("A   (Total Assets)", "A"),
        ("L   (Total Liabilities)", "L"),
        ("E   (Shareholders' Equity)", "E"),
        ("NCI (Noncontrolling Int.)", "NCI"),
        ("TE  (Temporary/Mezzanine)", "TE"),
        ("LE  (Total L & Equity)", "LE"),
        ("AC  (Current Assets)", "AC"),
        ("ANC (Noncurrent Assets)", "ANC"),
        ("LC  (Current Liabilities)", "LC"),
        ("LNC (Noncurrent Liabilities)", "LNC"),
    ]:
        cv = getattr(cur, attr, None) if cur else None
        pv = getattr(pri, attr, None) if pri else None
        c_str = _format_scaled_amount(cv, scale) if cv is not None else "—"
        p_str = _format_scaled_amount(pv, pri.scale if pri else scale) if pv is not None else "—"
        if attr == "L" and cv is None and cur and cur.LE is not None and cur.E is not None:
            c_str = "— (derived via F6)"
        if attr == "L" and pv is None and pri and pri.LE is not None and pri.E is not None:
            p_str = "— (derived via F6)"
        print(f"  {label:<30} | {c_str:<28} | {p_str}")

    # Steps 2 & 3: Formula Computations per Period
    by_role: Dict[str, List[CheckResult]] = defaultdict(list)
    for r in report.results:
        by_role[r.period_role].append(r)

    for role in ("current", "prior"):
        checks = by_role.get(role)
        if not checks:
            continue
        pdate = checks[0].period or "unknown"
        print(f"\n[STEPS 2-3] FORMULA CHECKS — {role.upper()} YEAR ({pdate})")
        print(
            f"  {'Formula':<7} | {'Equation':<22} | {'Status':<8} | {'Expected':<14} | {'Actual':<14} | {'Gap (Exp-Act)':<14} | {'Derived'}"
        )
        print("  " + "-" * 96)
        for r in checks:
            exp_s = _format_scaled_amount(r.expected, scale) if r.expected is not None else "—"
            act_s = _format_scaled_amount(r.actual, scale) if r.actual is not None else "—"
            gap_s = _format_scaled_amount(r.gap, scale) if r.gap is not None else "—"
            eq_s = _formula_expr(r.check_id, r.formula_used)
            print(
                f"  {r.formula_used:<7} | {eq_s:<22} | {r.status.upper():<8} | {exp_s:<14} | {act_s:<14} | {gap_s:<14} | {str(r.derived)}"
            )
            if r.skip_reason:
                print(f"          -> Skip reason: {r.skip_reason}")
            elif r.formula_variant_note:
                print(f"          -> Variant: {r.formula_variant_note}")

    # Steps 4 & 5: Triangulation & Diagnosis for Failed Checks
    failed_eq = [r for r in report.results if r.status == "fail" and r.check_id == "accounting_equation"]
    if failed_eq:
        print("\n[STEPS 4-5] TRIANGULATION & ROOT-CAUSE DIAGNOSIS")
        for r in failed_eq:
            print(f"  Period {r.period_role.upper()} ({r.period}) [{r.formula_used}]:")
            print(f"    Triangulation   : P (A=LE) = {r.triangulation.get('P', 'N/A').upper()}  |  Q (L+TE+E=LE) = {r.triangulation.get('Q', 'N/A').upper()}")
            print(f"    Blamed Metric   : {r.blamed}  (confidence: {r.blame_confidence})")
            if r.suggested_value is not None:
                print(f"    Suggested Value : {_fmt_dollars_and_scaled(r.suggested_value, scale)}")
            print("    Hypotheses      :")
            for h in r.hypotheses:
                print(f"      - {h}")
            print(f"    Explanation     : {r.explanation}")
    else:
        cur_eq = next((r for r in report.results if r.period_role == "current" and r.check_id == "accounting_equation"), None)
        if cur_eq:
            print("\n[STEPS 4-5] DIAGNOSIS")
            print(f"  {cur_eq.explanation}")

    # Step 6: Two-Year Interpretation
    print("\n[STEP 6] TWO-YEAR INTERPRETATION MATRIX")
    cur_main = next((r for r in report.results if r.period_role == "current" and r.check_id == "accounting_equation"), None)
    pri_main = next((r for r in report.results if r.period_role == "prior" and r.check_id == "accounting_equation"), None)
    print(
        f"  Current: {(cur_main.status.upper() if cur_main else 'N/A')}  |  "
        f"Prior: {(pri_main.status.upper() if pri_main else 'N/A')}  ==>  "
        f"Interpretation: {report.two_year_interpretation.upper()}"
    )

    # Step 7: Metric Confidence & Audit Decisions
    print("\n[STEP 7] METRIC CONFIDENCE & HUMAN-IN-THE-LOOP DECISIONS")
    print(f"  {'Metric':<20} | {'Base Conf':>9} | {'Delta':>7} | {'Final Conf':>10} | {'Decision':<12} | {'Reason'}")
    print("  " + "-" * 88)
    for m, md in report.metric_decisions.items():
        base_s = f"{md.base_confidence:.2f}" if md.base_confidence is not None else "-"
        delta_s = f"{md.confidence_delta:+.2f}" if md.confidence_delta is not None else "-"
        print(
            f"  {m:<20} | {base_s:>9} | {delta_s:>7} | {md.confidence:>10.2f} | {md.decision.upper():<12} | {md.reason or '-'}"
        )

    counts = report.summary["counts"]
    print(
        f"\n  Summary: PASS={counts['pass']}  FAIL={counts['fail']}  WARN={counts['warn']}  SKIPPED={counts['skipped']}  "
        f"| Escalated: {report.summary['metrics_escalated'] or 'None'}"
    )
    print("=" * 92)


def run_interactive_loop() -> int:
    cfg = load_config()
    catalog = _catalog_by_cik()
    ciks = [item["cik"] for item in catalog]
    _print_catalog(catalog)

    last_report_dict: Optional[Dict[str, Any]] = None

    while True:
        try:
            raw_cik = input("\nEnter CIK (or 'random', 'list', 'json', 'quit'): ").strip()
        except EOFError:
            break

        low = raw_cik.lower()
        if low in ("q", "quit", "exit"):
            print("Exiting interactive validation test.")
            break
        if low in ("list", "ls", "ciks"):
            _print_catalog(catalog)
            continue
        if low in ("json", "j"):
            if last_report_dict is None:
                print("Run a CIK first before requesting JSON output.")
            else:
                print(json.dumps(last_report_dict, indent=2))
            continue
        if low in ("random", "rand", "r", ""):
            chosen_cik = random.choice(ciks)
            print(f"  -> Picked random CIK: {chosen_cik}")
        else:
            chosen_cik = raw_cik

        try:
            yr_raw = input("Fiscal year (press Enter for latest/default): ").strip()
            fiscal_year = int(yr_raw) if yr_raw else None
        except ValueError:
            print("Invalid year entered; using default filing.")
            fiscal_year = None
        except EOFError:
            fiscal_year = None

        try:
            sim_raw = input(
                "Simulate error? [0=none, 1=wrong_column, 2=x1000, 3=sign_flip, 4=assets_typo, 5=wrong_row, 6=digit_transposition] (Enter=none): "
            ).strip()
        except EOFError:
            sim_raw = ""

        try:
            bs = load_balance_sheet(cik=chosen_cik, fiscal_year=fiscal_year)
            mutated_bs, sim_note = _apply_error_simulation(bs, sim_raw)
            report = validate_balance_sheet_input(mutated_bs, config=cfg)
            last_report_dict = report.to_dict()
            render_detailed_report(mutated_bs, report, cfg, sim_note)
        except Exception as exc:
            print(f"\n[ERROR] Could not validate CIK {chosen_cik!r}: {exc}")

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Interactive terminal tester for Accounting Equation Validation Engine.")
    parser.add_argument("--cik", default=None, help="Run non-interactively for a specific CIK (or 'random')")
    parser.add_argument("--year", type=int, default=None, help="Optional fiscal year")
    parser.add_argument("--simulate", default="none", help="Optional error simulation (wrong_column, x1000, sign_flip, assets_typo, wrong_row, digit_transposition)")
    parser.add_argument("--json", action="store_true", help="Also dump Section 11 JSON output")
    args = parser.parse_args(argv)

    if args.cik is None:
        return run_interactive_loop()

    cfg = load_config()
    catalog = _catalog_by_cik()
    cik_val = random.choice([item["cik"] for item in catalog]) if args.cik.lower() == "random" else args.cik
    bs = load_balance_sheet(cik=cik_val, fiscal_year=args.year)
    mutated_bs, sim_note = _apply_error_simulation(bs, args.simulate)
    report = validate_balance_sheet_input(mutated_bs, config=cfg)
    render_detailed_report(mutated_bs, report, cfg, sim_note)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

