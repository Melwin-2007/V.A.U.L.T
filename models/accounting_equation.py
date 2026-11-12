"""
7-Step Accounting Equation Validation Engine.

Implements the complete balance sheet validation workflow:
  Step 1: Normalize numbers to plain dollars & compute tolerance (2 * scale)
  Step 2: Select & order formulas (F1/F2/F3 -> F6 derived -> F7/F8 subtotals)
  Step 3: Check result (PASS / FAIL / WARN / SKIPPED)
  Step 4: Triangulate guilty side using P (Assets = L&E) and Q (L+E = L&E)
  Step 5: Diagnose root cause via 5 ordered hypotheses:
          1) Wrong column (prior-year value)
          2) Missing NCI / mezzanine equity
          3) Wrong row (matched another balance sheet line)
          4) Scale error (x1000 or /1000)
          5) Sign error (-expected)
  Step 6: Run across both columns (Current Year & Prior Year)
  Step 7: Compute action, update metric confidences, & append to immutable audit log
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIPPED = "SKIPPED"


SCALE_KEYWORDS: Dict[str, float] = {
    "ones": 1.0,
    "dollars": 1.0,
    "units": 1.0,
    "exact": 1.0,
    "thousands": 1_000.0,
    "in thousands": 1_000.0,
    "millions": 1_000_000.0,
    "in millions": 1_000_000.0,
    "billions": 1_000_000_000.0,
    "in billions": 1_000_000_000.0,
}


def resolve_scale(scale: Union[str, int, float]) -> float:
    """Resolve a table scale specifier (e.g. 'millions', 'in thousands', 1e6) to a float multiplier."""
    if isinstance(scale, (int, float)):
        val = float(scale)
        return val if val > 0 else 1.0
    s = str(scale).strip().lower()
    if s in SCALE_KEYWORDS:
        return SCALE_KEYWORDS[s]
    try:
        val = float(s)
        return val if val > 0 else 1.0
    except ValueError:
        return 1.0


def parse_number(value: Any, input_scale: Union[str, int, float] = 1.0) -> Optional[float]:
    """
    Step 1 helper: Parse raw numbers, formatted strings, and accounting brackets '(1,234)'
    into plain dollar floats, multiplying by `input_scale`.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None

    multiplier = resolve_scale(input_scale)

    if isinstance(value, (int, float)):
        if math.isnan(value) or math.isinf(value):
            return None
        return float(value) * multiplier

    s = str(value).strip()
    if not s or s in {"-", "--", "—", "N/A", "NA", "null", "None", "nan", "NaN"}:
        return None

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    # Strip currency symbols, spaces, and thousands commas
    s = re.sub(r"[$€£¥,\s]", "", s)
    if not s:
        return None

    try:
        num = float(s)
    except ValueError:
        return None

    if math.isnan(num) or math.isinf(num):
        return None

    if negative:
        num = -abs(num)
    return num * multiplier


@dataclass
class BalanceSheetPeriodInput:
    """Normalized or raw inputs for one balance sheet column (date/period)."""

    period_label: str
    assets: Optional[float] = None
    liabilities: Optional[float] = None
    equity: Optional[float] = None
    equity_with_nci: Optional[float] = None
    nci: Optional[float] = None
    mezzanine_equity: Optional[float] = None
    liabilities_and_equity: Optional[float] = None
    current_assets: Optional[float] = None
    noncurrent_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    noncurrent_liabilities: Optional[float] = None

    # All non-dimensional balance sheet rows for Step 5 "Wrong row" diagnosis
    line_items: Dict[str, float] = field(default_factory=dict)

    # `input_scale`: multiplier applied to numbers in this object if not already plain dollars
    # (1.0 when values come from SEC XBRL/companyfacts which are already in plain dollars).
    input_scale: Union[str, int, float] = 1.0
    # `reporting_scale`: table display unit (e.g. 1_000_000 for "in millions" / decimals=-6).
    # Tolerance = 2 * reporting_scale.
    reporting_scale: Union[str, int, float] = 1_000_000.0

    consolidated: bool = True
    notes: List[str] = field(default_factory=list)

    def normalized(self) -> "BalanceSheetPeriodInput":
        """Step 1: Return a copy with all monetary fields converted to plain dollars."""
        in_scale = resolve_scale(self.input_scale)
        rep_scale = resolve_scale(self.reporting_scale)

        norm_lines: Dict[str, float] = {}
        for k, v in self.line_items.items():
            pv = parse_number(v, in_scale)
            if pv is not None:
                norm_lines[k] = pv

        return BalanceSheetPeriodInput(
            period_label=self.period_label,
            assets=parse_number(self.assets, in_scale),
            liabilities=parse_number(self.liabilities, in_scale),
            equity=parse_number(self.equity, in_scale),
            equity_with_nci=parse_number(self.equity_with_nci, in_scale),
            nci=parse_number(self.nci, in_scale),
            mezzanine_equity=parse_number(self.mezzanine_equity, in_scale),
            liabilities_and_equity=parse_number(self.liabilities_and_equity, in_scale),
            current_assets=parse_number(self.current_assets, in_scale),
            noncurrent_assets=parse_number(self.noncurrent_assets, in_scale),
            current_liabilities=parse_number(self.current_liabilities, in_scale),
            noncurrent_liabilities=parse_number(self.noncurrent_liabilities, in_scale),
            line_items=norm_lines,
            input_scale=1.0,
            reporting_scale=rep_scale,
            consolidated=self.consolidated,
            notes=list(self.notes),
        )


@dataclass
class FormulaCheckResult:
    formula_id: str  # F1, F2, F3, F6, F7, F8
    formula_name: str
    expression: str
    status: CheckStatus
    lhs: Optional[float]
    rhs: Optional[float]
    gap: Optional[float]
    tolerance: float
    reason: str
    derived_fields: List[str] = field(default_factory=list)


@dataclass
class TriangulationResult:
    p_passed: Optional[bool]  # Assets == Total L&E
    q_passed: Optional[bool]  # Liabilities + Equity == Total L&E
    suspect_group: str        # "nobody", "liabilities_or_equity", "assets", "all_three", "unavailable"
    suspect_metrics: List[str]
    meaning: str


@dataclass
class DiagnosisHypothesis:
    rank: int
    hypothesis_type: str  # wrong_column | missing_nci_mezzanine | wrong_row | scale_error | sign_error
    metric: str           # assets | liabilities | equity | equation
    extracted_value: Optional[float]
    expected_value: Optional[float]
    matched_evidence: str
    suggestion: str
    confidence_weight: float


@dataclass
class StepTrace:
    step_number: int
    step_name: str
    status: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PeriodValidationResult:
    period_label: str
    status: CheckStatus
    reason: str
    tolerance: float
    reporting_scale: float
    normalized_inputs: Dict[str, Optional[float]]
    effective_equity: Optional[float]
    effective_equity_formula: str
    liabilities_derived: bool
    checks: List[FormulaCheckResult]
    triangulation: Optional[TriangulationResult]
    expected_values: Dict[str, Optional[float]]
    hypotheses: List[DiagnosisHypothesis]
    blamed_metric: Optional[str]
    is_ambiguous: bool
    suggested_fixes: Dict[str, float]
    action: str
    escalated_metrics: List[str]
    metric_confidences: Dict[str, float]
    steps: List[StepTrace]


@dataclass
class TwoYearValidationResult:
    cik: Optional[str]
    company_name: str
    overall_status: CheckStatus
    two_year_pattern: str
    two_year_meaning: str
    reason: str
    current_period: PeriodValidationResult
    prior_period: Optional[PeriodValidationResult]
    action: str
    escalated_metrics: List[str]
    metric_confidences: Dict[str, float]
    warnings: List[str] = field(default_factory=list)


@dataclass
class AuditLogEntry:
    timestamp_utc: str
    cik: Optional[str]
    company_name: str
    period_label: str
    overall_status: str
    reason: str
    blamed_metric: Optional[str]
    suggested_fixes: Dict[str, float]
    escalated_metrics: List[str]
    metric_confidences: Dict[str, float]
    ai_model_confidence: Optional[float]
    action: str
    reviewer_decision: str = "PENDING"
    reviewer_notes: str = ""


class AccountingEquationEngine:
    """
    Implements the 7-step Balance Sheet Accounting Equation Engine.
    """

    DEFAULT_BASE_CONFIDENCE = 0.85

    def __init__(
        self,
        base_confidence: float = DEFAULT_BASE_CONFIDENCE,
        audit_log_path: Optional[Union[str, Path]] = None,
    ) -> None:
        self.base_confidence = float(base_confidence)
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None

    @staticmethod
    def _within_tol(a: float, b: float, tol: float) -> bool:
        return abs(a - b) <= tol

    @staticmethod
    def _fmt_dollars(val: Optional[float]) -> str:
        if val is None:
            return "N/A"
        return f"${val:,.0f}"

    def _resolve_effective_equity(
        self, data: BalanceSheetPeriodInput
    ) -> Tuple[Optional[float], float, float, str, str]:
        """
        Resolve effective equity and determine whether F1, F2, or F3 applies.

        Returns:
            (effective_equity, nci_added, mezzanine_added, formula_id, formula_desc)
        """
        eq_parent = data.equity
        eq_total = data.equity_with_nci
        nci = data.nci
        mez = data.mezzanine_equity

        # Handle non-zero mezzanine/redeemable equity
        has_mez = mez is not None and abs(mez) > 0

        # Case 1: Both parent equity and total equity (with NCI) are reported
        if eq_parent is not None and eq_total is not None:
            # Avoid double counting NCI if eq_total already includes NCI
            implied_nci = eq_total - eq_parent
            effective_nci = nci if nci is not None else (implied_nci if abs(implied_nci) > 1e-6 else 0.0)
            has_nci = (nci is not None and abs(nci) > 0) or abs(implied_nci) > 1e-6
            base_plus_nci = eq_parent + effective_nci
            if has_mez:
                return (
                    base_plus_nci + mez,
                    effective_nci,
                    mez,
                    "F3",
                    "Assets = Liabilities + Equity + NCI + Mezzanine Equity",
                )
            if has_nci:
                return (
                    base_plus_nci,
                    effective_nci,
                    0.0,
                    "F2",
                    "Assets = Liabilities + Equity + NCI",
                )
            return (
                eq_parent,
                0.0,
                0.0,
                "F1",
                "Assets = Liabilities + Equity",
            )

        # Case 2: Parent equity + explicit NCI (without eq_total)
        if eq_parent is not None:
            has_nci = nci is not None and abs(nci) > 0
            eff = eq_parent + (nci or 0.0) + (mez or 0.0)
            if has_mez:
                return (
                    eff,
                    nci or 0.0,
                    mez or 0.0,
                    "F3",
                    "Assets = Liabilities + Equity + NCI + Mezzanine Equity"
                    if has_nci
                    else "Assets = Liabilities + Equity + Mezzanine Equity",
                )
            if has_nci:
                return (
                    eff,
                    nci or 0.0,
                    0.0,
                    "F2",
                    "Assets = Liabilities + Equity + NCI",
                )
            return (
                eq_parent,
                0.0,
                0.0,
                "F1",
                "Assets = Liabilities + Equity",
            )

        # Case 3: Only equity_with_nci is reported (already includes NCI)
        if eq_total is not None:
            eff = eq_total + (mez or 0.0)
            if has_mez:
                return (
                    eff,
                    0.0,
                    mez or 0.0,
                    "F3",
                    "Assets = Liabilities + Total Equity (incl. NCI) + Mezzanine Equity",
                )
            return (
                eq_total,
                0.0,
                0.0,
                "F1",
                "Assets = Liabilities + Equity",
            )

        return (None, 0.0, 0.0, "F1", "Assets = Liabilities + Equity")

    def validate_period(
        self,
        raw_input: BalanceSheetPeriodInput,
        prior_raw_input: Optional[BalanceSheetPeriodInput] = None,
    ) -> PeriodValidationResult:
        """Execute Steps 1 through 5 and Step 7 for a single balance sheet period."""
        steps: List[StepTrace] = []

        # ------------------------------------------------------------------
        # STEP 1: Get the numbers ready (Normalize to plain dollars + Tol)
        # ------------------------------------------------------------------
        data = raw_input.normalized()
        prior = prior_raw_input.normalized() if prior_raw_input is not None else None

        rep_scale = resolve_scale(data.reporting_scale)
        tolerance = 2.0 * rep_scale

        if not data.consolidated:
            data.notes.append("WARNING: Statement flagged as non-consolidated (parent-only).")

        steps.append(
            StepTrace(
                step_number=1,
                step_name="Normalize Numbers & Tolerance",
                status="DONE",
                summary=(
                    f"Normalized values to plain dollars (reporting scale=${rep_scale:,.0f}); "
                    f"tolerance = 2 x scale = {self._fmt_dollars(tolerance)}."
                ),
                details={
                    "period": data.period_label,
                    "reporting_scale": rep_scale,
                    "tolerance": tolerance,
                    "assets": data.assets,
                    "liabilities": data.liabilities,
                    "equity": data.equity,
                    "equity_with_nci": data.equity_with_nci,
                    "nci": data.nci,
                    "mezzanine_equity": data.mezzanine_equity,
                    "liabilities_and_equity": data.liabilities_and_equity,
                },
            )
        )

        # ------------------------------------------------------------------
        # STEP 2 & STEP 3: Select formulas (F6 derived if L missing, F1/F2/F3, F7/F8)
        # ------------------------------------------------------------------
        checks: List[FormulaCheckResult] = []
        eff_equity, nci_added, mez_added, eq_formula_id, eq_formula_desc = (
            self._resolve_effective_equity(data)
        )

        # F6: Derive Liabilities if missing (like Amazon / Nike)
        liabilities_derived = False
        effective_liabilities = data.liabilities
        if effective_liabilities is None and data.liabilities_and_equity is not None and eff_equity is not None:
            effective_liabilities = data.liabilities_and_equity - eff_equity
            liabilities_derived = True
            checks.append(
                FormulaCheckResult(
                    formula_id="F6",
                    formula_name="Derived Liabilities",
                    expression="Liabilities = (Total L&E) - Effective Equity",
                    status=CheckStatus.WARN,
                    lhs=effective_liabilities,
                    rhs=data.liabilities_and_equity - eff_equity,
                    gap=0.0,
                    tolerance=tolerance,
                    reason=(
                        f"Total Liabilities line not printed; derived Liabilities = "
                        f"{self._fmt_dollars(effective_liabilities)} from Total L&E - Equity. "
                        "Because it is derived, F1/F2/F3 is circular; rely on F7/F8."
                    ),
                    derived_fields=["liabilities"],
                )
            )

        # F1 / F2 / F3: Component Accounting Equation
        eq_check: FormulaCheckResult
        if data.assets is not None and effective_liabilities is not None and eff_equity is not None:
            rhs_eq = effective_liabilities + eff_equity
            gap_eq = data.assets - rhs_eq
            if liabilities_derived:
                # Derived from L&E - Equity, so F1 proves nothing by itself
                eq_status = CheckStatus.WARN if abs(gap_eq) <= tolerance else CheckStatus.FAIL
                eq_reason = (
                    f"{eq_formula_id} evaluated with derived Liabilities (F6): "
                    f"Assets ({self._fmt_dollars(data.assets)}) vs L+E ({self._fmt_dollars(rhs_eq)}), "
                    f"gap={self._fmt_dollars(gap_eq)} (circular check)."
                )
                eq_check = FormulaCheckResult(
                    formula_id=eq_formula_id,
                    formula_name="Accounting Equation (Derived Liabilities)",
                    expression=eq_formula_desc,
                    status=eq_status,
                    lhs=data.assets,
                    rhs=rhs_eq,
                    gap=gap_eq,
                    tolerance=tolerance,
                    reason=eq_reason,
                    derived_fields=["liabilities"],
                )
            else:
                eq_pass = abs(gap_eq) <= tolerance
                eq_status = CheckStatus.PASS if eq_pass else CheckStatus.FAIL
                eq_reason = (
                    f"{eq_formula_id} passed: Assets ({self._fmt_dollars(data.assets)}) = "
                    f"Liabilities ({self._fmt_dollars(effective_liabilities)}) + "
                    f"Effective Equity ({self._fmt_dollars(eff_equity)}); "
                    f"gap={self._fmt_dollars(gap_eq)} <= tolerance ({self._fmt_dollars(tolerance)})."
                    if eq_pass
                    else f"{eq_formula_id} failed: Assets ({self._fmt_dollars(data.assets)}) != "
                    f"Liabilities ({self._fmt_dollars(effective_liabilities)}) + "
                    f"Effective Equity ({self._fmt_dollars(eff_equity)}) = {self._fmt_dollars(rhs_eq)}; "
                    f"gap={self._fmt_dollars(gap_eq)} > tolerance ({self._fmt_dollars(tolerance)})."
                )
                eq_check = FormulaCheckResult(
                    formula_id=eq_formula_id,
                    formula_name="Fundamental Accounting Equation",
                    expression=eq_formula_desc,
                    status=eq_status,
                    lhs=data.assets,
                    rhs=rhs_eq,
                    gap=gap_eq,
                    tolerance=tolerance,
                    reason=eq_reason,
                )
        else:
            missing_eq = [
                n
                for n, v in (
                    ("Assets", data.assets),
                    ("Liabilities", effective_liabilities),
                    ("Equity", eff_equity),
                )
                if v is None
            ]
            eq_check = FormulaCheckResult(
                formula_id=eq_formula_id,
                formula_name="Fundamental Accounting Equation",
                expression=eq_formula_desc,
                status=CheckStatus.SKIPPED,
                lhs=data.assets,
                rhs=None,
                gap=None,
                tolerance=tolerance,
                reason=f"Skipped {eq_formula_id}: missing {', '.join(missing_eq)} (never mark missing inputs as PASS).",
            )
        checks.append(eq_check)

        # F7: Current + Non-Current Assets = Total Assets
        if data.assets is not None and data.current_assets is not None and data.noncurrent_assets is not None:
            rhs_f7 = data.current_assets + data.noncurrent_assets
            gap_f7 = data.assets - rhs_f7
            f7_pass = abs(gap_f7) <= tolerance
            checks.append(
                FormulaCheckResult(
                    formula_id="F7",
                    formula_name="Assets Subtotal Check",
                    expression="Total Assets = Current Assets + Non-Current Assets",
                    status=CheckStatus.PASS if f7_pass else CheckStatus.WARN,
                    lhs=data.assets,
                    rhs=rhs_f7,
                    gap=gap_f7,
                    tolerance=tolerance,
                    reason=(
                        f"F7 passed: Current ({self._fmt_dollars(data.current_assets)}) + "
                        f"Non-Current ({self._fmt_dollars(data.noncurrent_assets)}) = "
                        f"Total Assets ({self._fmt_dollars(data.assets)})."
                        if f7_pass
                        else f"F7 subtotal mismatch: Current + Non-Current ({self._fmt_dollars(rhs_f7)}) "
                        f"!= Total Assets ({self._fmt_dollars(data.assets)}), gap={self._fmt_dollars(gap_f7)}."
                    ),
                )
            )
        else:
            missing_f7 = [
                n
                for n, v in (
                    ("Total Assets", data.assets),
                    ("Current Assets", data.current_assets),
                    ("Non-Current Assets", data.noncurrent_assets),
                )
                if v is None
            ]
            checks.append(
                FormulaCheckResult(
                    formula_id="F7",
                    formula_name="Assets Subtotal Check",
                    expression="Total Assets = Current Assets + Non-Current Assets",
                    status=CheckStatus.SKIPPED,
                    lhs=data.assets,
                    rhs=None,
                    gap=None,
                    tolerance=tolerance,
                    reason=f"Skipped F7: {', '.join(missing_f7)} not printed as explicit subtotals.",
                )
            )

        # F8: Current + Non-Current Liabilities = Total Liabilities
        if (
            effective_liabilities is not None
            and data.current_liabilities is not None
            and data.noncurrent_liabilities is not None
        ):
            rhs_f8 = data.current_liabilities + data.noncurrent_liabilities
            gap_f8 = effective_liabilities - rhs_f8
            f8_pass = abs(gap_f8) <= tolerance
            checks.append(
                FormulaCheckResult(
                    formula_id="F8",
                    formula_name="Liabilities Subtotal Check",
                    expression="Total Liabilities = Current Liabilities + Non-Current Liabilities",
                    status=CheckStatus.PASS if f8_pass else CheckStatus.WARN,
                    lhs=effective_liabilities,
                    rhs=rhs_f8,
                    gap=gap_f8,
                    tolerance=tolerance,
                    reason=(
                        f"F8 passed: Current ({self._fmt_dollars(data.current_liabilities)}) + "
                        f"Non-Current ({self._fmt_dollars(data.noncurrent_liabilities)}) = "
                        f"Total Liabilities ({self._fmt_dollars(effective_liabilities)})."
                        if f8_pass
                        else f"F8 subtotal mismatch: Current + Non-Current ({self._fmt_dollars(rhs_f8)}) "
                        f"!= Total Liabilities ({self._fmt_dollars(effective_liabilities)}), gap={self._fmt_dollars(gap_f8)}."
                    ),
                    derived_fields=["liabilities"] if liabilities_derived else [],
                )
            )
        else:
            missing_f8 = [
                n
                for n, v in (
                    ("Total Liabilities", effective_liabilities),
                    ("Current Liabilities", data.current_liabilities),
                    ("Non-Current Liabilities", data.noncurrent_liabilities),
                )
                if v is None
            ]
            checks.append(
                FormulaCheckResult(
                    formula_id="F8",
                    formula_name="Liabilities Subtotal Check",
                    expression="Total Liabilities = Current Liabilities + Non-Current Liabilities",
                    status=CheckStatus.SKIPPED,
                    lhs=effective_liabilities,
                    rhs=None,
                    gap=None,
                    tolerance=tolerance,
                    reason=f"Skipped F8: {', '.join(missing_f8)} not printed as explicit subtotals.",
                )
            )

        # Determine Step 3 overall period status before diagnosis
        any_primary_fail = eq_check.status == CheckStatus.FAIL
        primary_checks_all_skipped = eq_check.status == CheckStatus.SKIPPED
        only_derived_checked = liabilities_derived
        subtotal_warn = any(
            c.formula_id in ("F7", "F8") and c.status == CheckStatus.WARN for c in checks
        )

        if any_primary_fail:
            period_status = CheckStatus.FAIL
        elif primary_checks_all_skipped:
            period_status = CheckStatus.SKIPPED
        elif only_derived_checked or subtotal_warn or not data.consolidated:
            period_status = CheckStatus.WARN
        elif eq_check.status == CheckStatus.PASS:
            period_status = CheckStatus.PASS
        else:
            period_status = CheckStatus.SKIPPED

        steps.append(
            StepTrace(
                step_number=2,
                step_name="Select & Execute Formulas",
                status="DONE",
                summary=f"Executed formulas {[c.formula_id for c in checks]} ({eq_formula_id}, then F7/F8).",
                details={
                    "formulas_run": [
                        {"id": c.formula_id, "status": c.status.value, "gap": c.gap}
                        for c in checks
                    ],
                    "liabilities_derived_f6": liabilities_derived,
                },
            )
        )

        primary_gap = eq_check.gap
        steps.append(
            StepTrace(
                step_number=3,
                step_name="Check Accounting Equation Result",
                status=period_status.value,
                summary=(
                    f"Result = {period_status.value}. "
                    + (
                        f"Primary gap = {self._fmt_dollars(primary_gap)} (tolerance {self._fmt_dollars(tolerance)})."
                        if primary_gap is not None
                        else "Missing required inputs."
                    )
                ),
                details={"status": period_status.value, "primary_gap": primary_gap, "tolerance": tolerance},
            )
        )

        # ------------------------------------------------------------------
        # STEP 4: If FAIL, triangulate who is guilty with P and Q
        # ------------------------------------------------------------------
        triangulation: Optional[TriangulationResult] = None
        if period_status == CheckStatus.FAIL:
            p_pass: Optional[bool] = None
            q_pass: Optional[bool] = None
            if data.assets is not None and data.liabilities_and_equity is not None:
                p_pass = self._within_tol(data.assets, data.liabilities_and_equity, tolerance)
            if (
                effective_liabilities is not None
                and eff_equity is not None
                and data.liabilities_and_equity is not None
                and not liabilities_derived
            ):
                q_pass = self._within_tol(
                    effective_liabilities + eff_equity, data.liabilities_and_equity, tolerance
                )

            if p_pass is True and q_pass is True:
                triangulation = TriangulationResult(
                    p_passed=True,
                    q_passed=True,
                    suspect_group="nobody",
                    suspect_metrics=[],
                    meaning="Both P (Assets = Total L&E) and Q (L + E = Total L&E) pass.",
                )
            elif p_pass is True and q_pass is False:
                triangulation = TriangulationResult(
                    p_passed=True,
                    q_passed=False,
                    suspect_group="liabilities_or_equity",
                    suspect_metrics=["liabilities", "equity"],
                    meaning="P passes (Assets matches L&E line) and Q fails -> Assets is correct; Liabilities or Equity is wrong.",
                )
            elif p_pass is False and q_pass is True:
                triangulation = TriangulationResult(
                    p_passed=False,
                    q_passed=True,
                    suspect_group="assets",
                    suspect_metrics=["assets"],
                    meaning="P fails and Q passes (right side agrees with itself) -> Assets is the odd one out.",
                )
            elif p_pass is False and q_pass is False:
                triangulation = TriangulationResult(
                    p_passed=False,
                    q_passed=False,
                    suspect_group="all_three",
                    suspect_metrics=["assets", "liabilities", "equity"],
                    meaning="Both P and Q fail -> multiple metrics wrong or wrong period; escalate all three.",
                )
            else:
                # L&E line unavailable; test all three
                triangulation = TriangulationResult(
                    p_passed=p_pass,
                    q_passed=q_pass,
                    suspect_group="unavailable",
                    suspect_metrics=["assets", "liabilities", "equity"],
                    meaning="Total L&E line unavailable for P/Q triangulation; testing all three metrics.",
                )

            steps.append(
                StepTrace(
                    step_number=4,
                    step_name="Triangulate Guilty Side (P & Q)",
                    status="DONE",
                    summary=triangulation.meaning,
                    details=asdict(triangulation),
                )
            )
        else:
            steps.append(
                StepTrace(
                    step_number=4,
                    step_name="Triangulate Guilty Side (P & Q)",
                    status="SKIPPED",
                    summary=f"Not needed because Step 3 status is {period_status.value}.",
                )
            )

        # ------------------------------------------------------------------
        # STEP 5: Diagnose why it is wrong (5 ordered guesses)
        # ------------------------------------------------------------------
        expected_values: Dict[str, Optional[float]] = {
            "assets": None,
            "liabilities": None,
            "equity": None,
        }
        hypotheses: List[DiagnosisHypothesis] = []
        blamed_metric: Optional[str] = None
        is_ambiguous = False
        suggested_fixes: Dict[str, float] = {}

        if period_status == CheckStatus.FAIL:
            # What each metric should be assuming the other side/items are right
            if effective_liabilities is not None and eff_equity is not None:
                expected_values["assets"] = effective_liabilities + eff_equity
            elif data.liabilities_and_equity is not None:
                expected_values["assets"] = data.liabilities_and_equity

            if data.assets is not None and eff_equity is not None:
                expected_values["liabilities"] = data.assets - eff_equity

            # Expected parent equity = Assets - Liabilities - nci_added - mez_added
            if data.assets is not None and effective_liabilities is not None:
                expected_values["equity"] = (
                    data.assets - effective_liabilities - nci_added - mez_added
                )

            # Determine extracted value map for suspects
            extracted_equity_val = (
                data.equity if data.equity is not None else data.equity_with_nci
            )
            extracted_map = {
                "assets": data.assets,
                "liabilities": effective_liabilities,
                "equity": extracted_equity_val,
            }

            suspects = (
                triangulation.suspect_metrics
                if triangulation and triangulation.suspect_metrics
                else ["assets", "liabilities", "equity"]
            )

            rank_counter = 1

            # Guess 1: Wrong column (picked last year's value)
            if prior is not None:
                prior_eq_eff, _, _, _, _ = self._resolve_effective_equity(prior)
                prior_map = {
                    "assets": prior.assets,
                    "liabilities": prior.liabilities
                    if prior.liabilities is not None
                    else (
                        prior.liabilities_and_equity - prior_eq_eff
                        if prior.liabilities_and_equity is not None and prior_eq_eff is not None
                        else None
                    ),
                    "equity": prior.equity if prior.equity is not None else prior.equity_with_nci,
                }
                for m in suspects:
                    ext = extracted_map.get(m)
                    exp = expected_values.get(m)
                    prv = prior_map.get(m)
                    if ext is not None and exp is not None and prv is not None:
                        if self._within_tol(ext, prv, tolerance) and not self._within_tol(ext, exp, tolerance):
                            hypotheses.append(
                                DiagnosisHypothesis(
                                    rank=rank_counter,
                                    hypothesis_type="wrong_column",
                                    metric=m,
                                    extracted_value=ext,
                                    expected_value=exp,
                                    matched_evidence=(
                                        f"Extracted {m} ({self._fmt_dollars(ext)}) equals prior-year "
                                        f"({prior.period_label}) value ({self._fmt_dollars(prv)})."
                                    ),
                                    suggestion=f"Use current-year {m} value {self._fmt_dollars(exp)}.",
                                    confidence_weight=0.95,
                                )
                            )
                            rank_counter += 1

            # Guess 2: Missing NCI / mezzanine equity
            # Applies if gap matches a known NCI or mezzanine line item or omitted NCI/mezzanine
            gap_val = eq_check.gap
            if gap_val is not None:
                nci_candidates: List[Tuple[str, float]] = []
                if data.nci is not None and abs(data.nci) > tolerance:
                    nci_candidates.append(("Noncontrolling Interest (NCI)", data.nci))
                if data.mezzanine_equity is not None and abs(data.mezzanine_equity) > tolerance:
                    nci_candidates.append(("Mezzanine / Redeemable Equity", data.mezzanine_equity))
                for row_name, row_val in data.line_items.items():
                    low = row_name.lower()
                    if any(
                        kw in low
                        for kw in ("noncontrolling", "minority", "redeemable", "temporary equity", "mezzanine")
                    ):
                        nci_candidates.append((row_name, row_val))

                seen_nci_matches = set()
                for cname, cval in nci_candidates:
                    if self._within_tol(abs(gap_val), abs(cval), tolerance) and cname not in seen_nci_matches:
                        seen_nci_matches.add(cname)
                        target_formula = "F3" if "redeem" in cname.lower() or "temp" in cname.lower() or "mezz" in cname.lower() else "F2"
                        hypotheses.append(
                            DiagnosisHypothesis(
                                rank=rank_counter,
                                hypothesis_type="missing_nci_mezzanine",
                                metric="equity",
                                extracted_value=extracted_equity_val,
                                expected_value=expected_values.get("equity"),
                                matched_evidence=(
                                    f"Gap ({self._fmt_dollars(gap_val)}) matches '{cname}' ({self._fmt_dollars(cval)})."
                                ),
                                suggestion=f"Switch formula to {target_formula} and include '{cname}'.",
                                confidence_weight=0.90,
                            )
                        )
                        rank_counter += 1

            # Guess 3: Wrong row (extracted value matches another row on the balance sheet,
            # or |gap| matches a balance sheet row)
            target_row_exclusions = {
                "assets": {"total assets", "assets", "total consolidated assets"},
                "liabilities": {"total liabilities", "liabilities", "total consolidated liabilities"},
                "equity": {
                    "total stockholders' equity",
                    "stockholders' equity",
                    "total equity",
                    "equity",
                    "total shareholders' equity",
                    "shareholders' equity",
                    "equity, attributable to parent",
                    "total equity, attributable to parent",
                    "equity attributable to owners of parent",
                    "total stockholders' equity (deficit)",
                },
            }
            for m in suspects:
                ext = extracted_map.get(m)
                exp = expected_values.get(m)
                if ext is None or exp is None or self._within_tol(ext, exp, tolerance):
                    continue
                for row_label, row_val in data.line_items.items():
                    if row_label.strip().lower() in target_row_exclusions.get(m, set()):
                        continue
                    # Avoid double-counting if this row is literally equal to prior-year value already matched
                    if self._within_tol(ext, row_val, tolerance):
                        hypotheses.append(
                            DiagnosisHypothesis(
                                rank=rank_counter,
                                hypothesis_type="wrong_row",
                                metric=m,
                                extracted_value=ext,
                                expected_value=exp,
                                matched_evidence=(
                                    f"Extracted {m} ({self._fmt_dollars(ext)}) matches balance sheet line "
                                    f"'{row_label}' ({self._fmt_dollars(row_val)})."
                                ),
                                suggestion=f"Replace mis-extracted '{row_label}' with true {m} ({self._fmt_dollars(exp)}).",
                                confidence_weight=0.88,
                            )
                        )
                        rank_counter += 1
                        break

            # Guess 4: Scale error (Value * 1000 or / 1000, or * 1e6 / 1e6)
            for m in suspects:
                ext = extracted_map.get(m)
                exp = expected_values.get(m)
                if ext is None or exp is None or abs(ext) < 1e-6 or self._within_tol(ext, exp, tolerance):
                    continue
                for factor_label, factor in (("x 1,000", 1_000.0), ("/ 1,000", 0.001), ("x 1,000,000", 1_000_000.0), ("/ 1,000,000", 1e-6)):
                    rescaled = ext * factor
                    # Relative check + tolerance check so /1000 errors don't falsely pass on huge tolerance
                    rel_diff = abs(rescaled - exp) / max(abs(exp), 1.0)
                    if abs(rescaled - exp) <= tolerance and rel_diff <= 0.01:
                        hypotheses.append(
                            DiagnosisHypothesis(
                                rank=rank_counter,
                                hypothesis_type="scale_error",
                                metric=m,
                                extracted_value=ext,
                                expected_value=exp,
                                matched_evidence=(
                                    f"Extracted {m} ({self._fmt_dollars(ext)}) {factor_label} = "
                                    f"{self._fmt_dollars(rescaled)}, matching expected {self._fmt_dollars(exp)}."
                                ),
                                suggestion=f"Rescale {m} ({factor_label}) to {self._fmt_dollars(exp)}.",
                                confidence_weight=0.92,
                            )
                        )
                        rank_counter += 1
                        break

            # Guess 5: Sign error (Value == -expected)
            for m in suspects:
                ext = extracted_map.get(m)
                exp = expected_values.get(m)
                if ext is None or exp is None or abs(ext) <= tolerance:
                    continue
                if self._within_tol(-ext, exp, tolerance):
                    hypotheses.append(
                        DiagnosisHypothesis(
                            rank=rank_counter,
                            hypothesis_type="sign_error",
                            metric=m,
                            extracted_value=ext,
                            expected_value=exp,
                            matched_evidence=(
                                f"Extracted {m} ({self._fmt_dollars(ext)}) is the exact negative of expected "
                                f"{self._fmt_dollars(exp)}."
                            ),
                            suggestion=f"Flip sign on {m} to {self._fmt_dollars(exp)}.",
                            confidence_weight=0.93,
                        )
                    )
                    rank_counter += 1

            # Decide whether diagnosis isolates one metric or is ambiguous
            blamed_candidates = sorted({h.metric for h in hypotheses})
            if len(blamed_candidates) == 1 and len(hypotheses) >= 1:
                # Single guilty metric identified!
                blamed_metric = blamed_candidates[0]
                is_ambiguous = False
                if expected_values.get(blamed_metric) is not None:
                    suggested_fixes[blamed_metric] = float(expected_values[blamed_metric])  # type: ignore[arg-type]
            elif len(suspects) == 1 and len(hypotheses) == 0:
                # Triangulation P/Q isolated Assets alone (e.g. P=False, Q=True), even if none of 5 guesses matched
                blamed_metric = suspects[0]
                is_ambiguous = False
                if expected_values.get(blamed_metric) is not None:
                    suggested_fixes[blamed_metric] = float(expected_values[blamed_metric])  # type: ignore[arg-type]
            else:
                is_ambiguous = True
                for m in suspects:
                    if expected_values.get(m) is not None:
                        suggested_fixes[m] = float(expected_values[m])  # type: ignore[arg-type]

            steps.append(
                StepTrace(
                    step_number=5,
                    step_name="Diagnose Root Cause (5 Hypotheses)",
                    status="DONE",
                    summary=(
                        f"Blamed metric: {blamed_metric} via {hypotheses[0].hypothesis_type} — {hypotheses[0].suggestion}"
                        if blamed_metric and hypotheses
                        else (
                            f"Triangulation blamed {blamed_metric}; no specific pattern match among 5 guesses."
                            if blamed_metric
                            else f"Ambiguous failure across {suspects} ({len(hypotheses)} hypotheses found)."
                        )
                    ),
                    details={
                        "expected_values": expected_values,
                        "hypotheses": [asdict(h) for h in hypotheses],
                        "blamed_metric": blamed_metric,
                        "is_ambiguous": is_ambiguous,
                    },
                )
            )
        else:
            steps.append(
                StepTrace(
                    step_number=5,
                    step_name="Diagnose Root Cause (5 Hypotheses)",
                    status="SKIPPED",
                    summary=f"No failure to diagnose (status={period_status.value}).",
                )
            )

        # ------------------------------------------------------------------
        # STEP 7 (Period-level): Confidence updates & Action decision
        # ------------------------------------------------------------------
        confidences = {
            "assets": self.base_confidence,
            "liabilities": self.base_confidence,
            "equity": self.base_confidence,
        }
        escalated_metrics: List[str] = []

        if period_status == CheckStatus.PASS:
            for k in confidences:
                confidences[k] = round(min(0.99, confidences[k] + 0.10), 4)
            action = "AUTO_APPROVE_ELIGIBLE"
            reason = (
                f"PASS: Balance sheet balances within tolerance ({self._fmt_dollars(tolerance)}). "
                f"{eq_formula_id} passed."
            )
        elif period_status == CheckStatus.FAIL:
            if blamed_metric and not is_ambiguous:
                confidences[blamed_metric] = round(max(0.05, confidences[blamed_metric] - 0.55), 4)
                escalated_metrics = [blamed_metric]
                action = "ESCALATE_SINGLE_METRIC"
                top_h = next((h for h in hypotheses if h.metric == blamed_metric), None)
                fix_val = suggested_fixes.get(blamed_metric)
                reason = (
                    f"FAIL: Blamed '{blamed_metric}' — "
                    + (f"{top_h.matched_evidence} {top_h.suggestion}" if top_h else f"Suggested fix: {self._fmt_dollars(fix_val)}.")
                )
            else:
                for k in confidences:
                    confidences[k] = round(max(0.10, confidences[k] - 0.40), 4)
                escalated_metrics = ["assets", "liabilities", "equity"]
                action = "ESCALATE_ALL"
                reason = (
                    "FAIL (Ambiguous): Accounting equation imbalance could not be isolated to a single metric; "
                    f"escalating all three ({len(hypotheses)} hypotheses)."
                )
        elif period_status == CheckStatus.WARN:
            for k in confidences:
                confidences[k] = round(max(0.40, confidences[k] - 0.08), 4)
            action = "WARN_NO_FORCED_ESCALATION"
            warn_msgs = [c.reason for c in checks if c.status == CheckStatus.WARN] + list(data.notes)
            reason = "WARN: " + (" | ".join(warn_msgs) if warn_msgs else "Derived or subtotal warning.")
        else:
            action = "SKIPPED_DEFER_TO_OTHER_CHECKS"
            skip_msgs = [c.reason for c in checks if c.status == CheckStatus.SKIPPED]
            reason = "SKIPPED: " + (" | ".join(skip_msgs[:2]) if skip_msgs else "Missing required inputs.")

        steps.append(
            StepTrace(
                step_number=7,
                step_name="Action & Metric Confidence Update",
                status="DONE",
                summary=f"Action={action}; escalated={escalated_metrics or 'none'}; confidences={confidences}",
                details={
                    "action": action,
                    "escalated_metrics": escalated_metrics,
                    "suggested_fixes": suggested_fixes,
                    "metric_confidences": confidences,
                },
            )
        )

        return PeriodValidationResult(
            period_label=data.period_label,
            status=period_status,
            reason=reason,
            tolerance=tolerance,
            reporting_scale=rep_scale,
            normalized_inputs={
                "assets": data.assets,
                "liabilities": effective_liabilities,
                "liabilities_printed": data.liabilities,
                "equity": data.equity,
                "equity_with_nci": data.equity_with_nci,
                "nci": nci_added if nci_added else data.nci,
                "mezzanine_equity": mez_added if mez_added else data.mezzanine_equity,
                "liabilities_and_equity": data.liabilities_and_equity,
                "current_assets": data.current_assets,
                "noncurrent_assets": data.noncurrent_assets,
                "current_liabilities": data.current_liabilities,
                "noncurrent_liabilities": data.noncurrent_liabilities,
            },
            effective_equity=eff_equity,
            effective_equity_formula=eq_formula_id,
            liabilities_derived=liabilities_derived,
            checks=checks,
            triangulation=triangulation,
            expected_values=expected_values,
            hypotheses=hypotheses,
            blamed_metric=blamed_metric,
            is_ambiguous=is_ambiguous,
            suggested_fixes=suggested_fixes,
            action=action,
            escalated_metrics=escalated_metrics,
            metric_confidences=confidences,
            steps=steps,
        )

    def validate_two_years(
        self,
        current_period: BalanceSheetPeriodInput,
        prior_period: Optional[BalanceSheetPeriodInput] = None,
        cik: Optional[str] = None,
        company_name: str = "Unknown Company",
        ai_model_confidence: Optional[float] = None,
        log_audit: bool = False,
    ) -> TwoYearValidationResult:
        """
        Execute Step 6 (both years) & Step 7 (final decision + optional audit log append).
        """
        cur_res = self.validate_period(current_period, prior_raw_input=prior_period)
        pri_res = (
            self.validate_period(prior_period, prior_raw_input=None)
            if prior_period is not None
            else None
        )

        warnings: List[str] = []
        if not current_period.consolidated:
            warnings.append("Current period statement is not flagged as consolidated.")
        warnings.extend(current_period.notes)

        if pri_res is None:
            pattern = f"{cur_res.status.value} / N/A"
            meaning = "Single period evaluated (no prior-year comparative column available)."
            overall = cur_res.status
        else:
            c_s = cur_res.status
            p_s = pri_res.status
            pattern = f"{c_s.value} / {p_s.value}"
            if c_s == CheckStatus.PASS and p_s == CheckStatus.PASS:
                meaning = "Clean (both current and prior year balance sheets pass)."
                overall = CheckStatus.PASS
            elif c_s == CheckStatus.FAIL and p_s == CheckStatus.PASS:
                meaning = "Mistake is in the current-year column (prior year passed cleanly)."
                overall = CheckStatus.FAIL
            elif c_s == CheckStatus.PASS and p_s == CheckStatus.FAIL:
                meaning = "Mistake is in the prior-year comparative column (current year passed)."
                overall = CheckStatus.FAIL
            elif c_s == CheckStatus.FAIL and p_s == CheckStatus.FAIL:
                meaning = "Systematic problem (both years failed — check scale, wrong table, or wrong company)."
                overall = CheckStatus.FAIL
            elif c_s == CheckStatus.SKIPPED and p_s == CheckStatus.SKIPPED:
                meaning = "Both periods skipped due to missing inputs."
                overall = CheckStatus.SKIPPED
            elif CheckStatus.FAIL in (c_s, p_s):
                meaning = "One period failed while the other could not be fully verified."
                overall = CheckStatus.FAIL
            elif CheckStatus.WARN in (c_s, p_s):
                meaning = "Passed with warnings (derived liabilities or subtotal warning)."
                overall = CheckStatus.WARN
            else:
                meaning = f"Current period {c_s.value}, prior period {p_s.value}."
                overall = c_s

        # Attach Step 6 trace onto current_period steps (in Step 6 position)
        step6 = StepTrace(
            step_number=6,
            step_name="Two-Year Comparative Check",
            status="DONE" if pri_res is not None else "SKIPPED",
            summary=f"{pattern} -> {meaning}",
            details={
                "current_period": cur_res.period_label,
                "current_status": cur_res.status.value,
                "prior_period": pri_res.period_label if pri_res else None,
                "prior_status": pri_res.status.value if pri_res else None,
                "pattern": pattern,
                "meaning": meaning,
            },
        )
        # Insert Step 6 right before Step 7
        if cur_res.steps and cur_res.steps[-1].step_number == 7:
            cur_res.steps.insert(-1, step6)
        else:
            cur_res.steps.append(step6)

        # If current passed and prior failed, escalate prior failure details cleanly
        action = cur_res.action
        escalated = list(cur_res.escalated_metrics)
        confidences = dict(cur_res.metric_confidences)
        reason = cur_res.reason

        if overall == CheckStatus.FAIL and cur_res.status == CheckStatus.PASS and pri_res is not None:
            action = pri_res.action
            escalated = list(pri_res.escalated_metrics)
            confidences = dict(pri_res.metric_confidences)
            reason = f"Current year passed, but Prior Year ({pri_res.period_label}) failed: {pri_res.reason}"
        elif overall == CheckStatus.FAIL and pri_res is not None and pri_res.status == CheckStatus.FAIL:
            reason = f"{cur_res.reason} [Two-year pattern: {meaning}]"

        result = TwoYearValidationResult(
            cik=cik,
            company_name=company_name,
            overall_status=overall,
            two_year_pattern=pattern,
            two_year_meaning=meaning,
            reason=reason,
            current_period=cur_res,
            prior_period=pri_res,
            action=action,
            escalated_metrics=escalated,
            metric_confidences=confidences,
            warnings=warnings,
        )

        if log_audit and self.audit_log_path is not None:
            self.append_audit_log(result, ai_model_confidence=ai_model_confidence)

        return result

    def append_audit_log(
        self,
        result: TwoYearValidationResult,
        ai_model_confidence: Optional[float] = None,
        reviewer_decision: str = "PENDING",
        reviewer_notes: str = "",
    ) -> Path:
        """Step 7: Append validation record to an append-only JSONL audit log."""
        if self.audit_log_path is None:
            raise ValueError("audit_log_path is not configured on AccountingEquationEngine.")

        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = AuditLogEntry(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            cik=result.cik,
            company_name=result.company_name,
            period_label=result.current_period.period_label,
            overall_status=result.overall_status.value,
            reason=result.reason,
            blamed_metric=result.current_period.blamed_metric,
            suggested_fixes=result.current_period.suggested_fixes,
            escalated_metrics=result.escalated_metrics,
            metric_confidences=result.metric_confidences,
            ai_model_confidence=ai_model_confidence,
            action=result.action,
            reviewer_decision=reviewer_decision,
            reviewer_notes=reviewer_notes,
        )
        with self.audit_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry)) + "\n")
        return self.audit_log_path
