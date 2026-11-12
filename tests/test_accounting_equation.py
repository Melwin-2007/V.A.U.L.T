"""
Comprehensive test suite for V.A.U.L.T 7-Step Accounting Equation Engine,
CIK-only Repository Data Loader, and Balance Sheet Validation AI Model.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from models.accounting_equation import (
    AccountingEquationEngine,
    BalanceSheetPeriodInput,
    CheckStatus,
    parse_number,
)
from models.cik_data_loader import RepoCIKDataLoader
from models.confidence_ai import BalanceSheetValidationAI


# ---------------------------------------------------------------------------
# Step 1: Normalization & Tolerance
# ---------------------------------------------------------------------------
def test_parse_number_brackets_and_millions():
    assert parse_number("(1,234)", "in millions") == -1_234_000_000.0
    assert parse_number("$352,583", "millions") == 352_583_000_000.0
    assert parse_number("62,146", 1_000_000) == 62_146_000_000.0
    assert parse_number("-", "millions") is None
    assert parse_number(None, "millions") is None


def test_tolerance_2x_scale_rounding_passes():
    engine = AccountingEquationEngine()
    period = BalanceSheetPeriodInput(
        period_label="2023-09-30",
        assets=352_585,          # +$2M rounding gap
        liabilities=290_437,
        equity=62_146,
        liabilities_and_equity=352_583,
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_period(period)
    # |gap| = 2M == tolerance (2M) -> PASS
    assert res.status == CheckStatus.PASS
    assert res.tolerance == 2_000_000.0


# ---------------------------------------------------------------------------
# Step 2 & 3: Formulas F1..F8 & SKIPPED
# ---------------------------------------------------------------------------
def test_apple_fy2023_example_pass():
    engine = AccountingEquationEngine()
    fy23 = BalanceSheetPeriodInput(
        period_label="2023-09-30",
        assets=352_583,
        liabilities=290_437,
        equity=62_146,
        liabilities_and_equity=352_583,
        current_assets=143_566,
        noncurrent_assets=209_017,
        current_liabilities=145_308,
        noncurrent_liabilities=145_129,
        input_scale="millions",
        reporting_scale="millions",
    )
    fy22 = BalanceSheetPeriodInput(
        period_label="2022-09-24",
        assets=352_755,
        liabilities=302_083,
        equity=50_672,
        liabilities_and_equity=352_755,
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_two_years(fy23, fy22, cik="0000320193", company_name="Apple Inc.")
    assert res.overall_status == CheckStatus.PASS
    assert res.two_year_pattern == "PASS / PASS"
    assert res.action == "AUTO_APPROVE_ELIGIBLE"
    # Confidence boost (+0.10 from 0.85 -> 0.95)
    assert res.metric_confidences["assets"] == pytest.approx(0.95)
    assert res.metric_confidences["liabilities"] == pytest.approx(0.95)
    assert res.metric_confidences["equity"] == pytest.approx(0.95)
    # F4, F1, F7, F8 all ran and passed
    chk_map = {c.formula_id: c for c in res.current_period.checks}
    assert chk_map["F4"].status == CheckStatus.PASS
    assert chk_map["F1"].status == CheckStatus.PASS
    assert chk_map["F7"].status == CheckStatus.PASS
    assert chk_map["F8"].status == CheckStatus.PASS


def test_missing_inputs_never_mark_pass():
    engine = AccountingEquationEngine()
    incomplete = BalanceSheetPeriodInput(
        period_label="2025-12-31",
        assets=100_000,
        liabilities=None,
        equity=None,
        liabilities_and_equity=None,
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_period(incomplete)
    assert res.status == CheckStatus.SKIPPED
    assert res.action == "SKIPPED_DEFER_TO_OTHER_CHECKS"


def test_f6_derived_liabilities_only_warns_when_f4_missing():
    engine = AccountingEquationEngine()
    # F4 skipped because Assets & L&E not directly verified? Wait: if L&E is present and Assets missing -> SKIPPED.
    # Subtotal mismatch with derived liabilities -> WARN
    p = BalanceSheetPeriodInput(
        period_label="2026-06-30",
        assets=1_095_689,
        liabilities=None,
        equity=551_620,
        liabilities_and_equity=1_095_689,
        current_assets=249_264,
        noncurrent_assets=700_000,  # intentional subtotal mismatch -> WARN
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_period(p)
    assert res.liabilities_derived is True
    assert res.status == CheckStatus.WARN
    assert res.normalized_inputs["liabilities"] == pytest.approx(544_069_000_000.0)


# ---------------------------------------------------------------------------
# Step 4 & 5: Triangulation (P & Q) + 5 Diagnostic Hypotheses
# ---------------------------------------------------------------------------
def test_apple_deliberate_wrong_column_error():
    """Exact example from specification: Apple FY2023 with Liabilities=302,083 (FY22 value)."""
    engine = AccountingEquationEngine()
    fy23_err = BalanceSheetPeriodInput(
        period_label="2023-09-30",
        assets=352_583,
        liabilities=302_083,  # deliberate prior-year value
        equity=62_146,
        liabilities_and_equity=352_583,
        input_scale="millions",
        reporting_scale="millions",
    )
    fy22 = BalanceSheetPeriodInput(
        period_label="2022-09-24",
        assets=352_755,
        liabilities=302_083,
        equity=50_672,
        liabilities_and_equity=352_755,
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_two_years(fy23_err, fy22, cik="0000320193", company_name="Apple Inc.")
    cur = res.current_period
    assert res.overall_status == CheckStatus.FAIL
    assert res.two_year_pattern == "FAIL / PASS"
    assert "current-year" in res.two_year_meaning.lower()

    # Step 4: P passes (Assets == L&E), Q fails (L+E != L&E)
    assert cur.triangulation is not None
    assert cur.triangulation.p_passed is True
    assert cur.triangulation.q_passed is False
    assert cur.triangulation.suspect_group == "liabilities_or_equity"

    # Step 5: Blames ONLY liabilities via wrong_column, suggests 290,437M
    assert cur.blamed_metric == "liabilities"
    assert cur.is_ambiguous is False
    assert any(h.hypothesis_type == "wrong_column" and h.metric == "liabilities" for h in cur.hypotheses)
    assert cur.suggested_fixes["liabilities"] == pytest.approx(290_437_000_000.0)

    # Step 7: Escalates ONLY liabilities and drops its confidence sharply
    assert res.escalated_metrics == ["liabilities"]
    assert res.action == "ESCALATE_SINGLE_METRIC"
    assert res.metric_confidences["liabilities"] < 0.40
    assert res.metric_confidences["assets"] == pytest.approx(0.85)


def test_missing_nci_diagnosis():
    engine = AccountingEquationEngine()
    p = BalanceSheetPeriodInput(
        period_label="2024-06-30",
        assets=112_832,
        liabilities=45_569,
        equity=66_468,
        # NCI=723 and Mezzanine=72 omitted from equity, but present in balance sheet lines
        liabilities_and_equity=112_832,
        line_items={"Noncontrolling Interest": 795},
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_period(p)
    assert res.status == CheckStatus.FAIL
    assert any(h.hypothesis_type == "missing_nci_mezzanine" for h in res.hypotheses)


def test_wrong_row_diagnosis():
    engine = AccountingEquationEngine()
    p = BalanceSheetPeriodInput(
        period_label="2023-09-30",
        assets=352_583,
        liabilities=145_308,  # mistakenly picked Total Current Liabilities!
        equity=62_146,
        liabilities_and_equity=352_583,
        line_items={"Total Current Liabilities": 145_308},
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_period(p)
    assert res.status == CheckStatus.FAIL
    assert res.blamed_metric == "liabilities"
    assert any(
        h.hypothesis_type == "wrong_row" and "Total Current Liabilities" in h.matched_evidence
        for h in res.hypotheses
    )


def test_scale_error_diagnosis():
    engine = AccountingEquationEngine()
    p = BalanceSheetPeriodInput(
        period_label="2023-09-30",
        assets=352_583_000_000.0,
        liabilities=290_437_000.0,  # off by x1000!
        equity=62_146_000_000.0,
        liabilities_and_equity=352_583_000_000.0,
        input_scale=1.0,
        reporting_scale=1_000_000.0,
    )
    res = engine.validate_period(p)
    assert res.status == CheckStatus.FAIL
    assert res.blamed_metric == "liabilities"
    assert any(h.hypothesis_type == "scale_error" for h in res.hypotheses)


def test_sign_error_diagnosis():
    engine = AccountingEquationEngine()
    p = BalanceSheetPeriodInput(
        period_label="2023-09-30",
        assets=352_583,
        liabilities=290_437,
        equity=-62_146,  # sign flipped!
        liabilities_and_equity=352_583,
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_period(p)
    assert res.status == CheckStatus.FAIL
    assert res.blamed_metric == "equity"
    assert any(h.hypothesis_type == "sign_error" and h.metric == "equity" for h in res.hypotheses)


def test_ambiguous_multi_error_escalates_all():
    engine = AccountingEquationEngine()
    p = BalanceSheetPeriodInput(
        period_label="2023-09-30",
        assets=410_000,
        liabilities=250_000,
        equity=80_000,
        liabilities_and_equity=352_583,
        input_scale="millions",
        reporting_scale="millions",
    )
    res = engine.validate_period(p)
    assert res.status == CheckStatus.FAIL
    assert res.triangulation is not None
    assert res.triangulation.suspect_group == "all_three"
    assert res.is_ambiguous is True
    assert res.escalated_metrics == ["assets", "liabilities", "equity"]
    assert res.action == "ESCALATE_ALL"


def test_step6_systematic_both_fail():
    engine = AccountingEquationEngine()
    c1 = BalanceSheetPeriodInput(
        period_label="2023", assets=352_583, liabilities=200_000, equity=62_146,
        liabilities_and_equity=352_583, input_scale="millions", reporting_scale="millions"
    )
    c2 = BalanceSheetPeriodInput(
        period_label="2022", assets=352_755, liabilities=200_000, equity=50_672,
        liabilities_and_equity=352_755, input_scale="millions", reporting_scale="millions"
    )
    res = engine.validate_two_years(c1, c2)
    assert res.overall_status == CheckStatus.FAIL
    assert res.two_year_pattern == "FAIL / FAIL"
    assert "systematic" in res.two_year_meaning.lower()


def test_audit_log_append_only(tmp_path: Path):
    log_file = tmp_path / "audit.jsonl"
    engine = AccountingEquationEngine(audit_log_path=log_file)
    p = BalanceSheetPeriodInput(
        period_label="2023-09-30",
        assets=352_583, liabilities=290_437, equity=62_146,
        liabilities_and_equity=352_583, input_scale="millions", reporting_scale="millions"
    )
    engine.validate_two_years(p, cik="0000320193", company_name="Apple Inc.", ai_model_confidence=0.99, log_audit=True)
    engine.validate_two_years(p, cik="0000320193", company_name="Apple Inc.", ai_model_confidence=0.99, log_audit=True)
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["cik"] == "0000320193"
    assert rec["reviewer_decision"] == "PENDING"


# ---------------------------------------------------------------------------
# CIK-Only Repo Loader & AI Model Integration Tests
# ---------------------------------------------------------------------------
def test_cik_loader_and_ai_real_repo_filings():
    ai = BalanceSheetValidationAI()

    # Apple (CIK 0000320193) - F4 + F1 + F7 + F8
    rep_aapl = ai.validate_cik(320193)
    assert rep_aapl.verdict == CheckStatus.PASS
    assert rep_aapl.model_confidence >= 0.85
    assert rep_aapl.cik == "0000320193"

    # Amazon (CIK 0001018724) - F6 derived liabilities + F4
    rep_amzn = ai.validate_cik("0001018724")
    assert rep_amzn.verdict == CheckStatus.PASS
    assert rep_amzn.validation_result.current_period.liabilities_derived is True
    assert rep_amzn.model_confidence >= 0.80

    # Tesla (CIK 0001318605) - F3 (NCI + Mezzanine equity)
    rep_tsla = ai.validate_cik(1318605)
    assert rep_tsla.verdict == CheckStatus.PASS
    assert rep_tsla.validation_result.current_period.effective_equity_formula == "F3"

    # Walt Disney (CIK 0001744489) - F2 (NCI) + F6 (derived liabilities)
    rep_dis = ai.validate_cik(1744489)
    assert rep_dis.verdict == CheckStatus.PASS
    assert rep_dis.validation_result.current_period.effective_equity_formula == "F2"
    assert rep_dis.validation_result.current_period.liabilities_derived is True

    # JPMorgan (CIK 0000019617) - Unclassified bank balance sheet
    rep_jpm = ai.validate_cik(19617)
    assert rep_jpm.verdict == CheckStatus.PASS

    # e.l.f. Beauty (CIK 1600033) - Loaded from entity/*_facts.json
    rep_elf = ai.validate_cik(1600033)
    assert rep_elf.verdict == CheckStatus.PASS

    # Logistic Properties of the Americas (CIK 1997711) - IFRS entity JSON
    rep_lpa = ai.validate_cik(1997711)
    assert rep_lpa.verdict == CheckStatus.PASS


def test_ai_detects_injected_apple_error_via_cik():
    ai = BalanceSheetValidationAI()
    ds = ai.loader.load_by_cik("0000320193", prefer_form="10-K")
    corrupted = copy.deepcopy(ds.current_period)
    corrupted.liabilities = ds.prior_period.liabilities  # 302,083M
    rep = ai.validate_dataset(ds, override_current=corrupted)

    assert rep.verdict == CheckStatus.FAIL
    assert rep.ai_predicted_verdict == "FAIL"
    assert rep.ai_predicted_root_cause == "WRONG_COLUMN"
    assert rep.model_confidence >= 0.85
    assert rep.validation_result.current_period.blamed_metric == "liabilities"
    assert rep.validation_result.escalated_metrics == ["liabilities"]

