"""Comprehensive pytest suite and error-injection harness for validation/."""

from __future__ import annotations

import copy
import subprocess
import sys
from decimal import Decimal
from typing import Any, Dict, List, Tuple

import pytest
from fastapi.testclient import TestClient

from validation import (
    BalanceSheetInput,
    BalanceSheetPeriodInput,
    load_balance_sheet,
    validate_accounting_equation,
    validate_balance_sheet_input,
)
from validation.api import app


def _make_apple_fy2023_clean() -> BalanceSheetInput:
    """Worked Example A baseline ($M unnormalized values with scale=1_000_000)."""
    prior = BalanceSheetPeriodInput(
        period_end="2022-09-24",
        A="352,755",
        L="302,083",
        E="50,672",
        LE="352,755",
        scale=1_000_000,
    )
    current = BalanceSheetPeriodInput(
        period_end="2023-09-30",
        A="352,583",
        L="290,437",
        E="62,146",
        LE="352,583",
        AC="143,566",
        ANC="209,017",
        LC="145,308",
        LNC="145,129",
        scale=1_000_000,
        rows={
            "Cash and cash equivalents": "29,965",
            "Total current assets": "143,566",
            "Total non-current assets": "209,017",
            "Total current liabilities": "145,308",
            "Total non-current liabilities": "145,129",
            "Retained earnings/(Accumulated deficit)": "(214)",
        },
    )
    return BalanceSheetInput(
        cik="0000320193",
        company="Apple Inc.",
        accession="0000320193-23-000106",
        fiscal_year=2023,
        form="10-K",
        current=current,
        prior=prior,
    )


def _get_check(report: Any, formula_or_id: str, role: str = "current") -> Any:
    results = report.results if hasattr(report, "results") else report["results"]
    for r in results:
        r_role = r.period_role if hasattr(r, "period_role") else r["period_role"]
        r_cid = r.check_id if hasattr(r, "check_id") else r["check_id"]
        r_f = r.formula_used if hasattr(r, "formula_used") else r["formula_used"]
        if r_role == role and (r_cid == formula_or_id or r_f == formula_or_id):
            return r
    raise KeyError(f"Check {formula_or_id!r} for role {role!r} not found")


# ---------------------------------------------------------------------------
# Section 14 Worked Examples A - I
# ---------------------------------------------------------------------------


def test_example_a_clean_pass_apple_fy2023():
    bs = _make_apple_fy2023_clean()
    report = validate_balance_sheet_input(bs)

    cur_eq = _get_check(report, "accounting_equation", "current")
    pri_eq = _get_check(report, "accounting_equation", "prior")

    assert cur_eq.status == "pass"
    assert cur_eq.formula_used == "F1"
    assert cur_eq.gap == Decimal(0)
    assert pri_eq.status == "pass"
    assert report.two_year_interpretation == "clean"

    for metric in ("total_assets", "total_liabilities", "equity"):
        dec = report.metric_decisions[metric]
        assert dec.decision == "auto_approve"
        assert dec.confidence == pytest.approx(0.93, abs=1e-4)  # 0.88 + 0.05


def test_example_b_wrong_column():
    bs = _make_apple_fy2023_clean()
    bs.current.L = "302,083"  # Prior-year liabilities copied into current column

    report = validate_balance_sheet_input(bs)
    cur_eq = _get_check(report, "accounting_equation", "current")

    assert cur_eq.status == "fail"
    assert cur_eq.gap == Decimal("-11646000000")
    assert cur_eq.triangulation == {"P": "pass", "Q": "fail"}
    assert cur_eq.blamed == "total_liabilities"
    assert cur_eq.blame_confidence == "high"
    assert cur_eq.suggested_value == Decimal("290437000000")
    assert any("wrong_column" in h for h in cur_eq.hypotheses)

    # Escalate ONLY Liabilities; Assets and Equity remain auto-approved
    assert report.metric_decisions["total_liabilities"].decision == "escalate"
    assert report.metric_decisions["total_liabilities"].confidence == pytest.approx(0.58, abs=1e-4)
    assert report.metric_decisions["total_assets"].decision == "auto_approve"
    assert report.metric_decisions["equity"].decision == "auto_approve"


def test_example_c_assets_wrong():
    bs = _make_apple_fy2023_clean()
    bs.current.A = "359,000"  # Typo on Total Assets

    report = validate_balance_sheet_input(bs)
    cur_eq = _get_check(report, "accounting_equation", "current")

    assert cur_eq.status == "fail"
    assert cur_eq.triangulation == {"P": "fail", "Q": "pass"}
    assert cur_eq.blamed == "total_assets"
    assert cur_eq.blame_confidence == "high"
    assert cur_eq.suggested_value == Decimal("352583000000")
    assert report.metric_decisions["total_assets"].decision == "escalate"
    assert report.metric_decisions["total_liabilities"].decision == "auto_approve"
    assert report.metric_decisions["equity"].decision == "auto_approve"


def test_example_d_scale_error():
    bs = _make_apple_fy2023_clean()
    bs.current.L = "290,437,000"  # $M value scaled x1000 twice

    report = validate_balance_sheet_input(bs)
    cur_eq = _get_check(report, "accounting_equation", "current")

    assert cur_eq.status == "fail"
    assert cur_eq.blamed == "total_liabilities"
    assert cur_eq.blame_confidence == "high"
    assert cur_eq.suggested_value == Decimal("290437000000")
    assert any("scale_error" in h for h in cur_eq.hypotheses)


def test_example_e_sign_error():
    bs = _make_apple_fy2023_clean()
    bs.current.E = "(62,146)"  # Parentheses negative on Equity

    report = validate_balance_sheet_input(bs)
    cur_eq = _get_check(report, "accounting_equation", "current")

    assert cur_eq.status == "fail"
    assert cur_eq.blamed == "equity"
    assert cur_eq.blame_confidence == "high"
    assert cur_eq.suggested_value == Decimal("62146000000")
    assert any("sign_error" in h for h in cur_eq.hypotheses)
    assert report.metric_decisions["equity"].decision == "escalate"
    assert report.metric_decisions["total_assets"].decision == "auto_approve"
    assert report.metric_decisions["total_liabilities"].decision == "auto_approve"


def test_example_f_missing_nci_switches_to_f2():
    period = BalanceSheetPeriodInput(
        period_end="2023-12-31",
        A=100,
        L=60,
        E=30,
        NCI=10,
        LE=100,
        scale=1_000_000,
    )
    bs = BalanceSheetInput(cik="0000000001", company="Test NCI Corp", current=period)
    report = validate_balance_sheet_input(bs)

    cur_eq = _get_check(report, "accounting_equation", "current")
    assert cur_eq.status == "pass"
    assert cur_eq.formula_used == "F2"
    assert cur_eq.gap == Decimal(0)
    assert "F2" in (cur_eq.formula_variant_note or cur_eq.explanation)


def test_example_g_derived_liabilities_amazon_style():
    period = BalanceSheetPeriodInput(
        period_end="2022-12-31",
        A="462,675",
        L=None,
        E="146,043",
        LE="462,675",
        AC="146,791",
        ANC="315,884",
        LC="155,393",
        LNC="161,239",
        scale=1_000_000,
    )
    bs = BalanceSheetInput(cik="0001018724", company="AMAZON COM INC", current=period)
    report = validate_balance_sheet_input(bs)

    cur_f4 = _get_check(report, "F4", "current")
    cur_eq = _get_check(report, "accounting_equation", "current")
    cur_f5 = _get_check(report, "F5", "current")
    cur_f7 = _get_check(report, "F7", "current")
    cur_f8 = _get_check(report, "F8", "current")

    assert cur_f4.status == "pass"
    assert cur_eq.derived is True
    assert cur_eq.status == "warn"  # NEVER clean PASS when L is derived!
    assert cur_f5.status == "warn"
    assert cur_f7.status == "pass"
    assert cur_f8.status == "pass"
    assert report.metric_decisions["total_liabilities"].confidence < report.metric_decisions["total_assets"].confidence


def test_example_h_missing_equity_skipped():
    bs = _make_apple_fy2023_clean()
    bs.current.E = None
    bs.prior = None

    report = validate_balance_sheet_input(bs)
    cur_eq = _get_check(report, "accounting_equation", "current")

    assert cur_eq.status == "skipped"
    assert cur_eq.skip_reason == "equity not extracted"
    assert cur_eq.metric_decisions["equity"].confidence_delta == 0.0


def test_example_i_both_periods_fail_systematic():
    bs = _make_apple_fy2023_clean()
    # 1000x scale error across BOTH current and prior columns
    bs.current.L = "290,437,000"
    bs.prior.L = "302,083,000"

    report = validate_balance_sheet_input(bs)
    assert report.two_year_interpretation == "systematic_problem"
    # Escalate ALL three metrics per Section 9 & Example I
    for metric in ("total_assets", "total_liabilities", "equity"):
        assert report.metric_decisions[metric].decision == "escalate"


# ---------------------------------------------------------------------------
# Section 15 Additional Required Unit & Boundary Tests
# ---------------------------------------------------------------------------


def test_tolerance_exact_boundary():
    # scale = 1_000_000 -> tolerance = 2_000_000 (already normalized in dollars)
    bs_pass = BalanceSheetInput(
        cik="0000000002",
        company="Boundary Pass Corp",
        current=BalanceSheetPeriodInput(
            period_end="2023-12-31",
            A=Decimal("100000000"),
            L=Decimal("60000000"),
            E=Decimal("38000000"),  # gap = +2_000_000 == 2 * scale
            LE=Decimal("100000000"),
            scale=1_000_000,
            already_normalized=True,
        ),
    )
    res_pass = _get_check(validate_balance_sheet_input(bs_pass), "accounting_equation")
    assert res_pass.gap == Decimal("2000000")
    assert res_pass.tolerance == Decimal("2000000")
    assert res_pass.status == "pass"

    bs_fail = copy.deepcopy(bs_pass)
    bs_fail.current.E = Decimal("37999999")  # gap = 2_000_001 == 2 * scale + 1
    res_fail = _get_check(validate_balance_sheet_input(bs_fail), "accounting_equation")
    assert res_fail.gap == Decimal("2000001")
    assert res_fail.status == "fail"


def test_period_mismatch_returns_skipped():
    bs = _make_apple_fy2023_clean()
    bs.prior = None
    bs.current.metric_periods = {
        "total_assets": "2023-09-30",
        "total_liabilities": "2023-09-24",  # Mismatched date!
        "equity": "2023-09-30",
        "total_liabilities_and_equity": "2023-09-30",
    }
    report = validate_balance_sheet_input(bs)
    cur_eq = _get_check(report, "accounting_equation", "current")
    assert cur_eq.status == "skipped"
    assert "period mismatch" in (cur_eq.skip_reason or "").lower()


def test_derived_l_never_clean_pass():
    period = BalanceSheetPeriodInput(
        period_end="2023-12-31",
        A=Decimal("1000"),
        L=None,
        E=Decimal("400"),
        LE=Decimal("1000"),
        scale=1,
        already_normalized=True,
    )
    bs = BalanceSheetInput(cik="0000000003", company="Derived Trap Co", current=period)
    report = validate_balance_sheet_input(bs)
    cur_eq = _get_check(report, "accounting_equation", "current")
    cur_f5 = _get_check(report, "F5", "current")
    assert cur_eq.derived is True
    assert cur_eq.status != "pass"
    assert cur_eq.status == "warn"
    assert cur_f5.status == "warn"


def test_non_consolidated_skipped():
    bs = _make_apple_fy2023_clean()
    bs.current.is_consolidated = False
    bs.prior = None
    report = validate_balance_sheet_input(bs)
    cur_eq = _get_check(report, "accounting_equation", "current")
    assert cur_eq.status == "skipped"
    assert "non-consolidated" in (cur_eq.skip_reason or "").lower()


# ---------------------------------------------------------------------------
# Integration Tests Against Real Repo Filings (Non-CompanyFacts) + CLI + API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cik,expected_status,expected_formula",
    [
        ("320193", "pass", "F1"),    # Apple Inc. 10-K
        ("789019", "pass", "F1"),    # Microsoft Corp 10-K
        ("1018724", "warn", "F1"),   # Amazon.com Inc 10-K (L derived via F6 -> WARN)
        ("1318605", "pass", "F3"),   # Tesla Inc. 10-Q (NCI + Mezzanine -> F3)
        ("19617", "pass", "F1"),     # JPMorgan Chase 10-K
        ("80172", "skipped", "F1"),  # National Presto (52/53-week date mismatch -> SKIPPED)
    ],
)
def test_real_repo_filings_via_adapter(cik: str, expected_status: str, expected_formula: str):
    out = validate_accounting_equation(cik=cik)
    cur_eq = _get_check(out, "accounting_equation", "current")
    assert cur_eq["status"] == expected_status
    assert cur_eq["formula_used"] == expected_formula


def test_fastapi_endpoint():
    client = TestClient(app)
    resp = client.post("/validate/accounting-equation", json={"cik": "320193", "fiscal_year": 2023})
    assert resp.status_code == 200
    body = resp.json()
    assert body["cik"] == "0000320193"
    assert body["two_year_interpretation"] == "clean"
    assert body["summary"]["metrics_escalated"] == []

    missing = client.post("/validate/accounting-equation", json={"cik": "9999999999"})
    assert missing.status_code == 404


def test_cli_execution():
    proc = subprocess.run(
        [sys.executable, "-m", "validation.accounting_equation", "--cik", "320193", "--year", "2023"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"cik": "0000320193"' in proc.stdout
    assert '"two_year_interpretation": "clean"' in proc.stdout


# ---------------------------------------------------------------------------
# Section 15 #4: Error-Injection Harness Across 5 Real Companies
# ---------------------------------------------------------------------------


def _transpose_digits(val: Decimal, scale: int) -> Decimal:
    """Swap two distinct adjacent digits in scaled integer units -> gap divisible by 9 * scale."""
    unscaled = int(val / Decimal(scale))
    s = list(str(abs(unscaled)))
    for i in range(len(s) - 1):
        if s[i] != s[i + 1]:
            s[i], s[i + 1] = s[i + 1], s[i]
            mutated = int("".join(s))
            if unscaled < 0:
                mutated = -mutated
            return Decimal(mutated) * Decimal(scale)
    return Decimal(unscaled + 9) * Decimal(scale)


def test_error_injection_harness():
    """Inject 6 error types across 5 real SEC filings and print precision/recall metrics."""
    base_ciks = ["320193", "789019", "1045810", "1318605", "19617"]
    baselines: List[BalanceSheetInput] = [load_balance_sheet(cik) for cik in base_ciks]

    # 1. Clean baseline -> False-alarm rate
    false_alarms = 0
    for bs in baselines:
        rep = validate_balance_sheet_input(bs)
        cur_eq = _get_check(rep, "accounting_equation", "current")
        if cur_eq.status == "fail" or rep.summary["metrics_escalated"]:
            false_alarms += 1
    false_alarm_rate = false_alarms / len(baselines)

    stats: Dict[str, Dict[str, int]] = {
        "prior_year_column": {"total": 0, "detected": 0, "correct_blame": 0},
        "x1000_scale": {"total": 0, "detected": 0, "correct_blame": 0},
        "sign_flip": {"total": 0, "detected": 0, "correct_blame": 0},
        "dropped_nci": {"total": 0, "detected": 0, "correct_blame": 0},
        "wrong_row": {"total": 0, "detected": 0, "correct_blame": 0},
        "digit_transposition": {"total": 0, "detected": 0, "correct_blame": 0},
    }

    for bs in baselines:
        assert bs.current is not None and bs.prior is not None

        # (a) Prior-year column error (on L if printed, else E on Amazon)
        c_prior = copy.deepcopy(bs)
        if c_prior.current.L is not None and c_prior.prior.L is not None and c_prior.current.L != c_prior.prior.L:
            c_prior.current.L = c_prior.prior.L
            target = "total_liabilities"
        else:
            c_prior.current.E = c_prior.prior.E
            target = "equity"
        rep = validate_balance_sheet_input(c_prior)
        eq = _get_check(rep, "accounting_equation", "current")
        stats["prior_year_column"]["total"] += 1
        if eq.status == "fail":
            stats["prior_year_column"]["detected"] += 1
        if eq.blamed == target:
            stats["prior_year_column"]["correct_blame"] += 1

        # (b) x1000 scale error (on L if printed, else E)
        c_scale = copy.deepcopy(bs)
        if c_scale.current.L is not None:
            c_scale.current.L = c_scale.current.L * Decimal(1000)
            target = "total_liabilities"
        else:
            c_scale.current.E = c_scale.current.E * Decimal(1000)
            target = "equity"
        rep = validate_balance_sheet_input(c_scale)
        eq = _get_check(rep, "accounting_equation", "current")
        stats["x1000_scale"]["total"] += 1
        if eq.status == "fail":
            stats["x1000_scale"]["detected"] += 1
        if eq.blamed == target:
            stats["x1000_scale"]["correct_blame"] += 1

        # (c) Sign flip (on Equity E)
        c_sign = copy.deepcopy(bs)
        c_sign.current.E = -c_sign.current.E
        rep = validate_balance_sheet_input(c_sign)
        eq = _get_check(rep, "accounting_equation", "current")
        stats["sign_flip"]["total"] += 1
        if eq.status == "fail":
            stats["sign_flip"]["detected"] += 1
        if eq.blamed == "equity":
            stats["sign_flip"]["correct_blame"] += 1

        # (d) Dropped NCI (ensure an NCI row exists in rows / split out NCI so F1 mismatch resolves to F2/F3 or flags missing_nci)
        c_nci = copy.deepcopy(bs)
        if c_nci.current.NCI is None or c_nci.current.NCI == 0:
            # Split 100M/100K of Equity into an unmapped NCI row on balance sheet
            nci_amt = Decimal(100) * Decimal(c_nci.current.scale or 1_000_000)
            c_nci.current.E = c_nci.current.E - nci_amt
            c_nci.current.rows["Noncontrolling interests"] = nci_amt
            c_nci.current.NCI = None
        else:
            c_nci.current.rows["Noncontrolling interests"] = c_nci.current.NCI
            c_nci.current.NCI = None
        rep = validate_balance_sheet_input(c_nci)
        eq = _get_check(rep, "accounting_equation", "current")
        stats["dropped_nci"]["total"] += 1
        if (
            (eq.formula_variant_note and ("F2" in eq.formula_variant_note or "F3" in eq.formula_variant_note))
            or any("missing_nci" in h for h in eq.hypotheses)
        ):
            stats["dropped_nci"]["detected"] += 1
            stats["dropped_nci"]["correct_blame"] += 1

        # (e) Wrong row (replace L with LC or A with AC)
        c_row = copy.deepcopy(bs)
        if c_row.current.L is not None and c_row.current.LC is not None:
            c_row.current.L = c_row.current.LC
            target = "total_liabilities"
        elif c_row.current.AC is not None:
            c_row.current.A = c_row.current.AC
            target = "total_assets"
        else:
            # Pick a real non-total row from c_row.current.rows for Assets
            first_lbl, first_val = next(iter(c_row.current.rows.items()))
            c_row.current.A = first_val
            target = "total_assets"
        rep = validate_balance_sheet_input(c_row)
        eq = _get_check(rep, "accounting_equation", "current")
        stats["wrong_row"]["total"] += 1
        if eq.status == "fail":
            stats["wrong_row"]["detected"] += 1
        if eq.blamed == target:
            stats["wrong_row"]["correct_blame"] += 1

        # (f) Digit transposition on Assets A (P=fail, Q=pass + div-by-9 hint)
        c_trans = copy.deepcopy(bs)
        c_trans.current.A = _transpose_digits(c_trans.current.A, c_trans.current.scale or 1)
        rep = validate_balance_sheet_input(c_trans)
        eq = _get_check(rep, "accounting_equation", "current")
        stats["digit_transposition"]["total"] += 1
        if eq.status == "fail":
            stats["digit_transposition"]["detected"] += 1
        if eq.blamed == "total_assets" and any("digit_transposition" in h for h in eq.hypotheses):
            stats["digit_transposition"]["correct_blame"] += 1

    print("\n=== Accounting Equation Error-Injection Benchmark (5 SEC Filings) ===")
    print(f"{'Error Type':<22} | {'N':>2} | {'Detection':>10} | {'Correct Blame':>14}")
    print("-" * 58)
    for err_type, s in stats.items():
        det_pct = 100.0 * s["detected"] / s["total"]
        blame_pct = 100.0 * s["correct_blame"] / s["total"]
        print(f"{err_type:<22} | {s['total']:>2} | {det_pct:>9.1f}% | {blame_pct:>13.1f}%")
    print("-" * 58)
    print(f"False-Alarm Rate on Clean Filings: {false_alarms}/{len(baselines)} ({100.0 * false_alarm_rate:.1f}%)")

    assert false_alarm_rate == 0.0
    for err_type, s in stats.items():
        assert s["detected"] == s["total"], f"Missed detection on {err_type}"
        assert s["correct_blame"] == s["total"], f"Missed correct blame on {err_type}"
