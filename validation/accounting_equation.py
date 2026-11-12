"""Deterministic Accounting Equation Validation Engine (pure Python, Decimal-exact, no LLM)."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .adapter import load_balance_sheet
from .models import (
    CANONICAL_METRIC_NAMES,
    BalanceSheetInput,
    BalanceSheetPeriodInput,
    CheckResult,
    MetricDecision,
    ValidationConfig,
    ValidationReport,
    decimal_to_json_number,
    load_config,
    to_decimal,
)

CORE_METRICS = ("total_assets", "total_liabilities", "equity")


def _format_scaled_amount(value: Optional[Decimal], scale: Optional[int]) -> str:
    if value is None:
        return "N/A"
    s = scale or 1
    scaled = value / Decimal(s)
    suffix = {1: "", 1_000: "K", 1_000_000: "M", 1_000_000_000: "B"}.get(s, "")
    if scaled == scaled.to_integral_value():
        num_str = f"{int(scaled):,}"
    else:
        num_str = f"{scaled:,.2f}"
    return f"{num_str}{suffix}" if suffix else f"${num_str}"


def _normalize_period(
    period: BalanceSheetPeriodInput,
    cfg: ValidationConfig,
    prior_fallback: Optional[BalanceSheetPeriodInput] = None,
) -> Tuple[BalanceSheetPeriodInput, Optional[str]]:
    """Convert raw period metrics into normalized Decimal dollars (pure, non-mutating copy)."""
    norm = deepcopy(period)
    scale = norm.scale
    if scale is None or int(scale) not in cfg.allowed_scales:
        return norm, f"invalid or missing table scale: {scale!r}"
    scale_int = int(scale)
    scale_dec = Decimal(scale_int)
    norm.scale = scale_int
    mult = Decimal(1) if norm.already_normalized else scale_dec

    for attr in ("A", "L", "E", "NCI", "TE", "LE", "AC", "ANC", "LC", "LNC"):
        raw = getattr(norm, attr)
        val = to_decimal(raw)
        setattr(norm, attr, val * mult if val is not None else None)

    norm_rows: Dict[str, Decimal] = {}
    for label, rval in (norm.rows or {}).items():
        dval = to_decimal(rval)
        if dval is not None:
            norm_rows[str(label)] = dval * mult
    norm.rows = norm_rows

    # Normalize prior_values (if already_normalized=False, apply current/prior scale)
    norm_prior_map: Dict[str, Decimal] = {}
    if norm.prior_values:
        prior_mult = Decimal(1) if norm.already_normalized else (
            Decimal(prior_fallback.scale)
            if (prior_fallback is not None and prior_fallback.scale and not prior_fallback.already_normalized)
            else scale_dec
        )
        for k, pv in norm.prior_values.items():
            canonical = CANONICAL_METRIC_NAMES.get(k, k)
            dval = to_decimal(pv)
            if dval is not None:
                norm_prior_map[canonical] = dval * prior_mult
    norm.prior_values = norm_prior_map

    norm_alts: Dict[str, List[Decimal]] = {}
    for k, cands in (norm.alt_candidates or {}).items():
        canonical = CANONICAL_METRIC_NAMES.get(k, k)
        out_list: List[Decimal] = []
        for c in cands:
            dval = to_decimal(c)
            if dval is not None:
                out_list.append(dval * mult)
        if out_list:
            norm_alts[canonical] = out_list
    norm.alt_candidates = norm_alts

    # Normalize Section 3 Rule 6 reporting basis
    if norm.e_includes_nci:
        norm.equity_reporting_basis = "total_including_nci"
        if norm.NCI is None:
            norm.NCI = Decimal(0)
    else:
        norm.equity_reporting_basis = norm.equity_reporting_basis or "parent_only"

    norm.already_normalized = True
    return norm, None


def _check_period_consistency(
    period: BalanceSheetPeriodInput,
    required_metrics: Tuple[str, ...],
) -> Optional[str]:
    """Return skip reason if terms used in one check come from different period_end dates."""
    if not period.metric_periods:
        return None
    dates = {m: period.metric_periods[m] for m in required_metrics if m in period.metric_periods and period.metric_periods[m]}
    unique = set(dates.values())
    if len(unique) > 1:
        detail = ", ".join(f"{m}={dt}" for m, dt in sorted(dates.items()))
        return f"period mismatch ({detail})"
    if period.period_end and unique and period.period_end not in unique:
        detail = ", ".join(f"{m}={dt}" for m, dt in sorted(dates.items()))
        return f"period mismatch (sheet={period.period_end}; {detail})"
    return None


def _e_total(period: BalanceSheetPeriodInput) -> Optional[Decimal]:
    if period.E is None:
        return None
    if period.e_includes_nci or period.NCI is None:
        return period.E
    return period.E + period.NCI


def _skipped_result(
    bs: BalanceSheetInput,
    period: BalanceSheetPeriodInput,
    period_role: str,
    check_id: str,
    formula_used: str,
    reason: str,
    cfg: ValidationConfig,
    derived: bool = False,
) -> CheckResult:
    decisions = _initial_decisions(period, cfg)
    return CheckResult(
        cik=bs.cik,
        company=bs.company,
        accession=bs.accession,
        period=period.period_end,
        period_role=period_role,
        check_id=check_id,
        formula_used=formula_used,
        status="skipped",
        expected=None,
        actual=None,
        gap=None,
        tolerance=(Decimal(cfg.tolerance_multiplier) * Decimal(period.scale)) if period.scale else None,
        triangulation={"P": "unavailable", "Q": "unavailable"},
        blamed=None,
        blame_confidence=None,
        hypotheses=[],
        suggested_value=None,
        derived=derived,
        explanation=f"{check_id} ({formula_used}) skipped for {period.period_end}: {reason}.",
        metric_decisions=decisions,
        skip_reason=reason,
    )


def _initial_decisions(period: BalanceSheetPeriodInput, cfg: ValidationConfig) -> Dict[str, MetricDecision]:
    out: Dict[str, MetricDecision] = {}
    for m in CORE_METRICS:
        base = float(period.base_confidences.get(m, cfg.default_base_confidence))
        out[m] = MetricDecision(
            confidence=base,
            decision="auto_approve" if base >= cfg.escalation_threshold else "escalate",
            reason=None if base >= cfg.escalation_threshold else "low base confidence",
            base_confidence=base,
            confidence_delta=0.0,
        )
    return out


def _apply_check_decisions(
    period: BalanceSheetPeriodInput,
    cfg: ValidationConfig,
    status: str,
    touched_metrics: Tuple[str, ...],
    blamed: Optional[str] = None,
    candidates: Optional[List[str]] = None,
    reason: Optional[str] = None,
    derived_L: bool = False,
) -> Dict[str, MetricDecision]:
    decisions = _initial_decisions(period, cfg)
    cand_set = set(candidates or [])
    for m in CORE_METRICS:
        base = float(period.base_confidences.get(m, cfg.default_base_confidence))
        delta = 0.0
        dec_reason: Optional[str] = None
        hard_escalate = False

        if status == "pass" and m in touched_metrics:
            delta += cfg.pass_delta
        elif status == "warn" and m in touched_metrics:
            delta += cfg.warn_delta
            dec_reason = reason or "warning"
        elif status == "fail":
            if blamed == m:
                delta += cfg.fail_single_blame_delta
                hard_escalate = True
                dec_reason = reason or f"{m} blamed in accounting equation failure"
            elif blamed == "ambiguous" and m in cand_set:
                delta += cfg.fail_ambiguous_delta
                hard_escalate = True
                dec_reason = reason or "ambiguous accounting equation failure"
            elif blamed and blamed not in CORE_METRICS and m in cand_set:
                delta += cfg.fail_ambiguous_delta
                hard_escalate = True
                dec_reason = reason or "accounting equation failure"
            else:
                delta += cfg.cleared_delta  # cleared by triangulation/diagnosis
        elif status == "skipped":
            delta += cfg.skipped_delta

        if m == "total_liabilities" and derived_L:
            delta += cfg.derived_delta
            if not dec_reason:
                dec_reason = "derived via F6"

        conf = min(cfg.confidence_cap, max(cfg.confidence_floor, base + delta))
        if hard_escalate:
            decision = "escalate"
        elif conf < cfg.escalation_threshold:
            decision = "escalate"
            if not dec_reason:
                dec_reason = f"confidence {conf:.2f} below threshold {cfg.escalation_threshold:.2f}"
        else:
            decision = "auto_approve"

        decisions[m] = MetricDecision(
            confidence=round(conf, 4),
            decision=decision,
            reason=dec_reason if decision == "escalate" else None,
            base_confidence=base,
            confidence_delta=round(delta, 4),
        )
    return decisions


def _is_canonical_grand_total_label(label: str) -> bool:
    norm = " ".join(label.lower().replace(",", " ").replace("'", "").split())
    return norm in {
        "assets",
        "total assets",
        "liabilities",
        "total liabilities",
        "stockholders equity",
        "total stockholders equity",
        "shareholders equity",
        "total shareholders equity",
        "total equity",
        "liabilities and stockholders equity",
        "total liabilities and stockholders equity",
        "total liabilities and shareholders equity",
        "total liabilities and equity",
    }


def _find_matching_nci_or_te_in_rows(
    period: BalanceSheetPeriodInput,
    gap: Decimal,
    tolerance: Decimal,
) -> Optional[Tuple[str, Decimal, str]]:
    """Check if unmapped NCI or mezzanine row explains the gap within tolerance."""
    for label, val in period.rows.items():
        low = label.lower()
        kind: Optional[str] = None
        if any(k in low for k in ("noncontrolling", "non-controlling", "minority interest")):
            kind = "F2"
        elif any(k in low for k in ("redeemable", "mezzanine", "temporary equity")):
            kind = "F3"
        if kind and abs(gap - val) <= tolerance:
            return (label, val, kind)
    return None


def _diagnose_candidates(
    period: BalanceSheetPeriodInput,
    candidates: List[str],
    implied: Dict[str, Decimal],
    gap: Decimal,
    tolerance: Decimal,
    cfg: ValidationConfig,
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], Dict[str, Optional[Decimal]], Optional[str]]:
    """Run Section 8 hypotheses in exact configured order for each candidate metric.

    Returns:
        strong_hyps: metric -> list of strong matching hypothesis strings (hypotheses 1..5)
        all_hyps: metric -> list of all matching hypothesis strings (1..6)
        suggestions: metric -> suggested Decimal correction value
        weak_hint: optional digit_transposition message
    """
    strong_hyps: Dict[str, List[str]] = {m: [] for m in candidates}
    all_hyps: Dict[str, List[str]] = {m: [] for m in candidates}
    suggestions: Dict[str, Optional[Decimal]] = {m: None for m in candidates}
    weak_hint: Optional[str] = None

    metric_val_map: Dict[str, Optional[Decimal]] = {
        "total_assets": period.A,
        "total_liabilities": period.L,
        "equity": period.E,
    }
    filtered_rows: List[Tuple[str, Decimal]] = [
        (lbl, rval) for lbl, rval in period.rows.items() if not _is_canonical_grand_total_label(lbl)
    ]

    # Precompute digit transposition test (equation-level weak hint)
    if period.scale and gap != 0:
        unscaled = gap / Decimal(period.scale)
        if unscaled == unscaled.to_integral_value():
            int_gap = int(abs(unscaled))
            if int_gap != 0 and int_gap % 9 == 0:
                weak_hint = (
                    f"digit_transposition: unscaled gap {int(unscaled):,} is divisible by 9 (weak hint only)"
                )

    for m in candidates:
        val = metric_val_map.get(m)
        x_star = implied.get(m)
        if val is None or x_star is None:
            continue

        def _record_strong(msg: str, sug: Optional[Decimal]) -> None:
            strong_hyps[m].append(msg)
            all_hyps[m].append(msg)
            if suggestions[m] is None and sug is not None:
                suggestions[m] = sug

        for hyp_name in cfg.hypothesis_order:
            if hyp_name == "wrong_column":
                pv = period.prior_values.get(m)
                if pv is not None and abs(val - pv) <= tolerance and abs(val - x_star) > tolerance:
                    _record_strong(
                        f"wrong_column: extracted {m} equals prior-year value ({_format_scaled_amount(pv, period.scale)})",
                        x_star,
                    )
                for cand in period.alt_candidates.get(m, []):
                    if abs(cand - x_star) <= tolerance and abs(val - x_star) > tolerance:
                        _record_strong(
                            f"wrong_column: alternative candidate {_format_scaled_amount(cand, period.scale)} resolves equation",
                            cand,
                        )
                        break

            elif hyp_name == "missing_nci_or_te":
                # Triggered if unrecorded NCI/TE in input or rows matches the gap (for L/E candidates or general)
                matched = False
                if period.NCI is not None and period.NCI != 0 and abs(gap - period.NCI) <= tolerance:
                    _record_strong(
                        f"missing_nci_or_te: gap matches noncontrolling interest ({_format_scaled_amount(period.NCI, period.scale)}); switch to F2",
                        x_star,
                    )
                    matched = True
                if not matched and period.TE is not None and period.TE != 0 and abs(gap - period.TE) <= tolerance:
                    _record_strong(
                        f"missing_nci_or_te: gap matches temporary/mezzanine equity ({_format_scaled_amount(period.TE, period.scale)}); switch to F3",
                        x_star,
                    )
                    matched = True
                if not matched:
                    row_hit = _find_matching_nci_or_te_in_rows(period, gap, tolerance)
                    if row_hit:
                        r_lbl, r_val, f_rec = row_hit
                        _record_strong(
                            f"missing_nci_or_te: gap matches balance sheet row '{r_lbl}' ({_format_scaled_amount(r_val, period.scale)}); switch to {f_rec}",
                            x_star,
                        )

            elif hyp_name == "wrong_row":
                row_matches: List[str] = []
                # Case A: X* matches a single parsed row in rows
                for lbl, rval in filtered_rows:
                    if abs(rval - x_star) <= tolerance and abs(val - x_star) > tolerance:
                        row_matches.append(
                            f"wrong_row: implied {m} matches row '{lbl}' ({_format_scaled_amount(rval, period.scale)})"
                        )
                        break
                # Case B: X* matches sum of two parsed rows in rows (e.g., Current + Noncurrent Liabilities)
                if not row_matches and len(filtered_rows) >= 2:
                    for (l1, v1), (l2, v2) in itertools.combinations(filtered_rows, 2):
                        if abs((v1 + v2) - x_star) <= tolerance and abs(val - x_star) > tolerance:
                            row_matches.append(
                                f"wrong_row: implied {m} matches sum of '{l1}' + '{l2}' ({_format_scaled_amount(v1 + v2, period.scale)})"
                            )
                            break
                # Case C: Extracted X matches a non-grand-total line row in rows (e.g. picked LiabilitiesCurrent instead of Liabilities)
                if not row_matches:
                    picked_rows = [lbl for lbl, rval in filtered_rows if abs(val - rval) <= tolerance]
                    gap_rows = [lbl for lbl, rval in filtered_rows if abs(abs(rval) - abs(gap)) <= tolerance]
                    if picked_rows:
                        if gap_rows and picked_rows[0] != gap_rows[0]:
                            row_matches.append(
                                f"wrong_row: extracted {m} matches sub-row '{picked_rows[0]}' and gap matches '{gap_rows[0]}'"
                            )
                        else:
                            row_matches.append(
                                f"wrong_row: extracted {m} matches sub-row '{picked_rows[0]}' instead of total"
                            )
                for msg in row_matches:
                    _record_strong(msg, x_star)

            elif hyp_name == "scale_error":
                if val != 0:
                    if abs(val * Decimal(1000) - x_star) <= tolerance:
                        _record_strong(
                            f"scale_error: extracted {m} x 1000 equals implied value ({_format_scaled_amount(x_star, period.scale)})",
                            x_star,
                        )
                    elif abs((val / Decimal(1000)) - x_star) <= tolerance:
                        _record_strong(
                            f"scale_error: extracted {m} / 1000 equals implied value ({_format_scaled_amount(x_star, period.scale)})",
                            x_star,
                        )

            elif hyp_name == "sign_error":
                if val != 0 and abs((-val) - x_star) <= tolerance:
                    _record_strong(
                        f"sign_error: negating extracted {m} ({_format_scaled_amount(-val, period.scale)}) reconciles equation",
                        x_star,
                    )

            elif hyp_name == "digit_transposition":
                if weak_hint is not None:
                    all_hyps[m].append(weak_hint)

    return strong_hyps, all_hyps, suggestions, weak_hint


def _validate_single_period(
    bs: BalanceSheetInput,
    raw_period: BalanceSheetPeriodInput,
    period_role: str,
    cfg: ValidationConfig,
    prior_raw: Optional[BalanceSheetPeriodInput] = None,
) -> List[CheckResult]:
    """Run ordered checks F4 -> F1/F2/F3 -> F5 -> F7 -> F8 (plus F6 derivation if L missing) on one period."""
    period, scale_err = _normalize_period(raw_period, cfg, prior_fallback=prior_raw)
    if scale_err:
        return [
            _skipped_result(bs, period, period_role, "accounting_equation", "F1", scale_err, cfg)
        ]

    scale = period.scale or 1
    tolerance = Decimal(cfg.tolerance_multiplier) * Decimal(scale)

    if not period.is_consolidated:
        return [
            _skipped_result(
                bs,
                period,
                period_role,
                "accounting_equation",
                "F1",
                "non-consolidated statement (consolidated statement required)",
                cfg,
            )
        ]

    # Step 5a: F6 derivation when Total Liabilities (L) is omitted (Amazon-style)
    derived_L = bool(period.derived_L)
    if period.L is None and "F6" in cfg.enabled_formulas and period.LE is not None and period.E is not None:
        etot = _e_total(period)
        f6_terms = ("total_liabilities_and_equity", "equity")
        if period.NCI is not None and not period.e_includes_nci:
            f6_terms = f6_terms + ("noncontrolling_interest",)
        if period.TE is not None:
            f6_terms = f6_terms + ("temporary_equity",)
        if _check_period_consistency(period, f6_terms) is None and etot is not None:
            period.L = period.LE - etot - (period.TE or Decimal(0))
            derived_L = True

    results: List[CheckResult] = []
    enabled = set(cfg.enabled_formulas)

    # 1) F4: A = LE (always run first)
    if "F4" in enabled:
        pm_err = _check_period_consistency(period, ("total_assets", "total_liabilities_and_equity"))
        if pm_err:
            results.append(_skipped_result(bs, period, period_role, "total_l_and_e_check", "F4", pm_err, cfg))
        elif period.A is None or period.LE is None:
            missing_lbl = "total assets not extracted" if period.A is None else "total liabilities and equity (LE) not printed"
            results.append(_skipped_result(bs, period, period_role, "total_l_and_e_check", "F4", missing_lbl, cfg))
        else:
            gap_f4 = period.A - period.LE
            st_f4 = "pass" if abs(gap_f4) <= tolerance else "fail"
            exp_f4 = (
                f"Assets {_format_scaled_amount(period.A, scale)} matches Total L&E {_format_scaled_amount(period.LE, scale)} (gap {_format_scaled_amount(gap_f4, scale)})."
                if st_f4 == "pass"
                else f"Assets {_format_scaled_amount(period.A, scale)} disagrees with Total L&E {_format_scaled_amount(period.LE, scale)} (gap {_format_scaled_amount(gap_f4, scale)})."
            )
            results.append(
                CheckResult(
                    cik=bs.cik,
                    company=bs.company,
                    accession=bs.accession,
                    period=period.period_end,
                    period_role=period_role,
                    check_id="total_l_and_e_check",
                    formula_used="F4",
                    status=st_f4,
                    expected=period.LE,
                    actual=period.A,
                    gap=gap_f4,
                    tolerance=tolerance,
                    triangulation={"P": st_f4, "Q": "not_run"},
                    blamed=None if st_f4 == "pass" else "ambiguous",
                    blame_confidence=None if st_f4 == "pass" else "ambiguous",
                    hypotheses=[],
                    suggested_value=period.LE if st_f4 == "fail" else None,
                    derived=False,
                    explanation=exp_f4,
                    metric_decisions=_apply_check_decisions(
                        period,
                        cfg,
                        st_f4,
                        ("total_assets",),
                        blamed=None if st_f4 == "pass" else "ambiguous",
                        candidates=["total_assets"] if st_f4 == "fail" else None,
                        reason=exp_f4 if st_f4 == "fail" else None,
                    ),
                )
            )

    # 2) Core Accounting Equation: F1 / F2 / F3
    has_nci = (period.NCI is not None and period.NCI != 0) and (not period.e_includes_nci)
    has_te = period.TE is not None and period.TE != 0
    if has_te:
        chosen_formula = "F3"
    elif has_nci:
        chosen_formula = "F2"
    else:
        chosen_formula = "F1"

    eq_terms = ["total_assets", "total_liabilities", "equity"]
    if has_nci:
        eq_terms.append("noncontrolling_interest")
    if has_te:
        eq_terms.append("temporary_equity")

    pm_eq = _check_period_consistency(period, tuple(eq_terms))
    if pm_eq:
        results.append(_skipped_result(bs, period, period_role, "accounting_equation", chosen_formula, pm_eq, cfg, derived=derived_L))
    elif period.A is None:
        results.append(_skipped_result(bs, period, period_role, "accounting_equation", chosen_formula, "assets not extracted", cfg, derived=derived_L))
    elif period.E is None:
        results.append(_skipped_result(bs, period, period_role, "accounting_equation", chosen_formula, "equity not extracted", cfg, derived=derived_L))
    elif period.L is None:
        results.append(_skipped_result(bs, period, period_role, "accounting_equation", chosen_formula, "total liabilities not extracted and LE missing", cfg, derived=derived_L))
    else:
        nci_add = Decimal(0) if (period.e_includes_nci or period.NCI is None) else period.NCI
        te_add = period.TE or Decimal(0)
        formula_variant_note: Optional[str] = None

        # Check plain F1 first if NCI/TE are present to detect/log variant switch cleanly (Section 5 & Example F)
        plain_rhs = period.L + period.E
        plain_gap = period.A - plain_rhs
        if (has_nci or has_te) and abs(plain_gap) > tolerance:
            rhs_variant = period.L + period.E + nci_add + te_add
            if abs(period.A - rhs_variant) <= tolerance:
                formula_variant_note = f"formula variant mismatch on F1; formula variant {chosen_formula} used"

        rhs_full = period.L + period.E + nci_add + te_add
        gap_eq = period.A - rhs_full

        # Inspect rows for unmapped NCI / TE before blaming anyone whenever NCI or TE isn't mapped yet
        if abs(gap_eq) > tolerance and (not has_nci or not has_te):
            row_hit = _find_matching_nci_or_te_in_rows(period, gap_eq, tolerance)
            if row_hit:
                r_lbl, r_val, f_rec = row_hit
                prev_formula = chosen_formula
                # Preserve F3 if TE already exists or triggered F3
                chosen_formula = "F3" if (has_te or f_rec == "F3") else "F2"
                rhs_full = rhs_full + r_val
                gap_eq = period.A - rhs_full
                formula_variant_note = (
                    f"formula variant mismatch on {prev_formula} (matched row '{r_lbl}'); "
                    f"formula variant {chosen_formula} used"
                )

        if abs(gap_eq) <= tolerance:
            if derived_L:
                status_eq = "warn"
                exp_eq = (
                    f"L derived via F6 ({_format_scaled_amount(period.L, scale)}); "
                    f"only derived value checked by {chosen_formula} (Rely on F4/F7/F8)."
                )
            else:
                status_eq = "pass"
                basis_note = " (E includes NCI)" if period.e_includes_nci else ""
                var_note = f" [{formula_variant_note}]" if formula_variant_note else (
                    f" [formula variant {chosen_formula} used]" if chosen_formula in ("F2", "F3") else ""
                )
                exp_eq = (
                    f"Assets {_format_scaled_amount(period.A, scale)} matches RHS "
                    f"{_format_scaled_amount(rhs_full, scale)} under {chosen_formula}{basis_note}{var_note}."
                )
            results.append(
                CheckResult(
                    cik=bs.cik,
                    company=bs.company,
                    accession=bs.accession,
                    period=period.period_end,
                    period_role=period_role,
                    check_id="accounting_equation",
                    formula_used=chosen_formula,
                    status=status_eq,
                    expected=period.A,
                    actual=rhs_full,
                    gap=gap_eq,
                    tolerance=tolerance,
                    triangulation={
                        "P": ("pass" if period.LE is not None and abs(period.A - period.LE) <= tolerance else ("fail" if period.LE is not None else "unavailable")),
                        "Q": ("pass" if period.LE is not None and abs(rhs_full - period.LE) <= tolerance else ("fail" if period.LE is not None else "unavailable")),
                    },
                    blamed=None,
                    blame_confidence=None,
                    hypotheses=[],
                    suggested_value=None,
                    derived=derived_L,
                    explanation=exp_eq,
                    metric_decisions=_apply_check_decisions(
                        period,
                        cfg,
                        status_eq,
                        CORE_METRICS,
                        reason="only derived value checked" if derived_L else None,
                        derived_L=derived_L,
                    ),
                    formula_variant_note=formula_variant_note or (f"formula variant {chosen_formula} used" if chosen_formula in ("F2", "F3") else None),
                )
            )
        else:
            # Step 7: Triangulation (P and Q)
            le_available = period.LE is not None
            if le_available:
                p_res = "pass" if abs(period.A - period.LE) <= tolerance else "fail"
                q_res = "pass" if abs(rhs_full - period.LE) <= tolerance else "fail"
                tri = {"P": p_res, "Q": q_res}
                if p_res == "pass" and q_res == "fail":
                    candidates = ["total_liabilities", "equity"]
                elif p_res == "fail" and q_res == "pass":
                    candidates = ["total_assets"]
                else:
                    candidates = ["total_assets", "total_liabilities", "equity"]
            else:
                tri = {"P": "unavailable", "Q": "unavailable"}
                candidates = ["total_assets", "total_liabilities", "equity"]

            # Implied values (Section 8)
            implied = {
                "total_assets": rhs_full,
                "total_liabilities": period.A - te_add - period.E - nci_add,
                "equity": period.A - period.L - te_add - nci_add,
            }

            strong_hyps, all_hyps, cand_suggestions, weak_hint = _diagnose_candidates(
                period=period,
                candidates=candidates,
                implied=implied,
                gap=gap_eq,
                tolerance=tolerance,
                cfg=cfg,
            )

            # Determine final blame
            blamed: str
            blame_conf: str
            flat_hyps: List[str] = []
            suggested_val: Optional[Decimal] = None

            if le_available and tri["P"] == "fail" and tri["Q"] == "fail":
                # P=fail, Q=fail -> multiple errors or wrong table/period -> escalate all
                blamed = "ambiguous"
                blame_conf = "ambiguous"
                for c in candidates:
                    flat_hyps.extend(all_hyps.get(c, []))
                if not flat_hyps:
                    flat_hyps = ["multiple_errors_or_wrong_table: both P (A=LE) and Q (L+E=LE) failed"]
            elif len(candidates) == 1:
                # Triangulation isolated a single candidate (e.g., P=fail, Q=pass -> total_assets)
                only_m = candidates[0]
                blamed = only_m
                blame_conf = "high"
                suggested_val = cand_suggestions.get(only_m) or implied[only_m]
                flat_hyps = list(all_hyps.get(only_m, []))
                if not flat_hyps:
                    flat_hyps = [
                        f"triangulation_isolated_{only_m}: Total L&E agrees with right side while {only_m} disagrees"
                    ]
            else:
                matched_strong = [c for c in candidates if strong_hyps.get(c)]
                if len(matched_strong) == 1:
                    blamed = matched_strong[0]
                    blame_conf = "high" if le_available else "low"
                    suggested_val = cand_suggestions.get(blamed) or implied[blamed]
                    flat_hyps = list(all_hyps.get(blamed, []))
                else:
                    blamed = "ambiguous"
                    blame_conf = "ambiguous"
                    for c in candidates:
                        flat_hyps.extend(all_hyps.get(c, []))
                    if not any(strong_hyps.get(c) for c in candidates):
                        base_msg = "no single-term fix found"
                        flat_hyps = [base_msg] + ([weak_hint] if weak_hint else [])

            # Deduplicate hypotheses preserving order
            seen_h: set[str] = set()
            dedup_hyps: List[str] = []
            for h in flat_hyps:
                if h not in seen_h:
                    seen_h.add(h)
                    dedup_hyps.append(h)

            # Build deterministic template explanation
            exp_parts = [
                f"Assets {_format_scaled_amount(period.A, scale)} vs Liabilities + Equity = {_format_scaled_amount(rhs_full, scale)} (gap {_format_scaled_amount(gap_eq, scale)})."
            ]
            if not le_available:
                exp_parts.append("Total L&E line is unavailable, so triangulation could not run.")
            elif tri["P"] == "pass" and tri["Q"] == "fail":
                exp_parts.append("Assets matches Total L&E, so the right side is wrong.")
            elif tri["P"] == "fail" and tri["Q"] == "pass":
                exp_parts.append("Right side matches Total L&E, so Assets stands alone.")
            elif tri["P"] == "fail" and tri["Q"] == "fail":
                exp_parts.append("Both P (A=LE) and Q (L+E=LE) failed; possible multiple errors or wrong table/period.")

            if blamed != "ambiguous":
                top_reason = dedup_hyps[0] if dedup_hyps else "isolated by triangulation"
                exp_parts.append(
                    f"Blame {blamed} ({blame_conf} confidence): {top_reason}. Suggested value: {_format_scaled_amount(suggested_val, scale)}."
                )
            else:
                exp_parts.append(
                    f"Blame ambiguous across {', '.join(candidates)}: {'; '.join(dedup_hyps[:2])}."
                )

            results.append(
                CheckResult(
                    cik=bs.cik,
                    company=bs.company,
                    accession=bs.accession,
                    period=period.period_end,
                    period_role=period_role,
                    check_id="accounting_equation",
                    formula_used=chosen_formula,
                    status="fail",
                    expected=period.A,
                    actual=rhs_full,
                    gap=gap_eq,
                    tolerance=tolerance,
                    triangulation=tri,
                    blamed=blamed,
                    blame_confidence=blame_conf,
                    hypotheses=dedup_hyps,
                    suggested_value=suggested_val,
                    derived=derived_L,
                    explanation=" ".join(exp_parts),
                    metric_decisions=_apply_check_decisions(
                        period,
                        cfg,
                        "fail",
                        CORE_METRICS,
                        blamed=blamed,
                        candidates=candidates,
                        reason=dedup_hyps[0] if dedup_hyps else "accounting equation mismatch",
                        derived_L=derived_L,
                    ),
                    candidate_hypotheses={k: list(v) for k, v in all_hyps.items() if v},
                    candidate_suggestions={k: cand_suggestions.get(k) for k in candidates},
                )
            )

    # 3) F5: L + TE + E_total = LE
    eq_check = next((r for r in results if r.check_id == "accounting_equation"), None)
    resolved_right_blame: Optional[str] = (
        eq_check.blamed
        if (eq_check is not None and eq_check.status == "fail" and eq_check.blamed in ("total_liabilities", "equity"))
        else None
    )
    if "F5" in enabled:
        f5_terms = ["total_liabilities", "equity", "total_liabilities_and_equity"]
        if has_nci:
            f5_terms.append("noncontrolling_interest")
        if has_te:
            f5_terms.append("temporary_equity")
        pm_f5 = _check_period_consistency(period, tuple(f5_terms))
        if pm_f5:
            results.append(_skipped_result(bs, period, period_role, "right_side_check", "F5", pm_f5, cfg, derived=derived_L))
        elif period.L is None or period.E is None or period.LE is None:
            results.append(
                _skipped_result(
                    bs,
                    period,
                    period_role,
                    "right_side_check",
                    "F5",
                    "requires L, E, and LE",
                    cfg,
                    derived=derived_L,
                )
            )
        else:
            nci_add = Decimal(0) if (period.e_includes_nci or period.NCI is None) else period.NCI
            te_add = period.TE or Decimal(0)
            rhs_f5 = period.L + te_add + period.E + nci_add
            gap_f5 = rhs_f5 - period.LE
            if abs(gap_f5) <= tolerance:
                st_f5 = "warn" if derived_L else "pass"
                exp_f5 = (
                    f"Only derived value checked in F5 (L derived via F6); trivially reconciles to Total L&E."
                    if derived_L
                    else f"L + E (+ NCI/TE) = {_format_scaled_amount(rhs_f5, scale)} matches Total L&E {_format_scaled_amount(period.LE, scale)}."
                )
            else:
                st_f5 = "fail"
                exp_f5 = f"Right side {_format_scaled_amount(rhs_f5, scale)} disagrees with Total L&E {_format_scaled_amount(period.LE, scale)} (gap {_format_scaled_amount(gap_f5, scale)})."
            f5_blamed = None if st_f5 != "fail" else (resolved_right_blame or "ambiguous")
            f5_blame_conf = None if st_f5 != "fail" else (
                eq_check.blame_confidence if resolved_right_blame and eq_check else "ambiguous"
            )
            results.append(
                CheckResult(
                    cik=bs.cik,
                    company=bs.company,
                    accession=bs.accession,
                    period=period.period_end,
                    period_role=period_role,
                    check_id="right_side_check",
                    formula_used="F5",
                    status=st_f5,
                    expected=period.LE,
                    actual=rhs_f5,
                    gap=gap_f5,
                    tolerance=tolerance,
                    triangulation={"P": "not_run", "Q": "pass" if st_f5 != "fail" else "fail"},
                    blamed=f5_blamed,
                    blame_confidence=f5_blame_conf,
                    hypotheses=[],
                    suggested_value=None,
                    derived=derived_L,
                    explanation=exp_f5,
                    metric_decisions=_apply_check_decisions(
                        period,
                        cfg,
                        st_f5,
                        ("total_liabilities", "equity"),
                        blamed=f5_blamed,
                        candidates=[f5_blamed] if resolved_right_blame else (["total_liabilities", "equity"] if st_f5 == "fail" else None),
                        reason=exp_f5 if st_f5 != "pass" else None,
                        derived_L=derived_L,
                    ),
                )
            )

    # 4) F7: A = AC + ANC
    if "F7" in enabled:
        pm_f7 = _check_period_consistency(period, ("total_assets", "current_assets", "noncurrent_assets"))
        if pm_f7:
            results.append(_skipped_result(bs, period, period_role, "assets_subtotal_check", "F7", pm_f7, cfg))
        elif period.A is None or period.AC is None or period.ANC is None:
            results.append(
                _skipped_result(
                    bs,
                    period,
                    period_role,
                    "assets_subtotal_check",
                    "F7",
                    "current or non-current assets subtotal not printed",
                    cfg,
                )
            )
        else:
            sum_a = period.AC + period.ANC
            gap_f7 = period.A - sum_a
            st_f7 = "pass" if abs(gap_f7) <= tolerance else "fail"
            exp_f7 = (
                f"Assets {_format_scaled_amount(period.A, scale)} = Current ({_format_scaled_amount(period.AC, scale)}) + Non-current ({_format_scaled_amount(period.ANC, scale)})."
                if st_f7 == "pass"
                else f"Assets {_format_scaled_amount(period.A, scale)} != Current + Non-current ({_format_scaled_amount(sum_a, scale)}), gap {_format_scaled_amount(gap_f7, scale)}."
            )
            results.append(
                CheckResult(
                    cik=bs.cik,
                    company=bs.company,
                    accession=bs.accession,
                    period=period.period_end,
                    period_role=period_role,
                    check_id="assets_subtotal_check",
                    formula_used="F7",
                    status=st_f7,
                    expected=period.A,
                    actual=sum_a,
                    gap=gap_f7,
                    tolerance=tolerance,
                    triangulation={"P": "not_run", "Q": "not_run"},
                    blamed=None if st_f7 == "pass" else "total_assets",
                    blame_confidence=None if st_f7 == "pass" else "low",
                    hypotheses=[] if st_f7 == "pass" else ["subtotal_mismatch: AC + ANC != A"],
                    suggested_value=sum_a if st_f7 == "fail" else None,
                    derived=False,
                    explanation=exp_f7,
                    metric_decisions=_apply_check_decisions(
                        period,
                        cfg,
                        st_f7,
                        ("total_assets",),
                        blamed=None if st_f7 == "pass" else "total_assets",
                        candidates=["total_assets"] if st_f7 == "fail" else None,
                        reason=exp_f7 if st_f7 == "fail" else None,
                    ),
                )
            )

    # 5) F8: L = LC + LNC
    if "F8" in enabled:
        pm_f8 = _check_period_consistency(period, ("total_liabilities", "current_liabilities", "noncurrent_liabilities"))
        if pm_f8:
            results.append(_skipped_result(bs, period, period_role, "liabilities_subtotal_check", "F8", pm_f8, cfg, derived=derived_L))
        elif period.L is None or period.LC is None or period.LNC is None:
            results.append(
                _skipped_result(
                    bs,
                    period,
                    period_role,
                    "liabilities_subtotal_check",
                    "F8",
                    "current or non-current liabilities subtotal not printed",
                    cfg,
                    derived=derived_L,
                )
            )
        else:
            sum_l = period.LC + period.LNC
            gap_f8 = period.L - sum_l
            st_f8 = "pass" if abs(gap_f8) <= tolerance else "fail"
            exp_f8 = (
                f"Liabilities {_format_scaled_amount(period.L, scale)} = Current ({_format_scaled_amount(period.LC, scale)}) + Non-current ({_format_scaled_amount(period.LNC, scale)})."
                if st_f8 == "pass"
                else f"Liabilities {_format_scaled_amount(period.L, scale)} != Current + Non-current ({_format_scaled_amount(sum_l, scale)}), gap {_format_scaled_amount(gap_f8, scale)}."
            )
            results.append(
                CheckResult(
                    cik=bs.cik,
                    company=bs.company,
                    accession=bs.accession,
                    period=period.period_end,
                    period_role=period_role,
                    check_id="liabilities_subtotal_check",
                    formula_used="F8",
                    status=st_f8,
                    expected=period.L,
                    actual=sum_l,
                    gap=gap_f8,
                    tolerance=tolerance,
                    triangulation={"P": "not_run", "Q": "not_run"},
                    blamed=None if st_f8 == "pass" else "total_liabilities",
                    blame_confidence=None if st_f8 == "pass" else "low",
                    hypotheses=[] if st_f8 == "pass" else ["subtotal_mismatch: LC + LNC != L"],
                    suggested_value=sum_l if st_f8 == "fail" else None,
                    derived=derived_L,
                    explanation=exp_f8,
                    metric_decisions=_apply_check_decisions(
                        period,
                        cfg,
                        st_f8,
                        ("total_liabilities",),
                        blamed=None if st_f8 == "pass" else "total_liabilities",
                        candidates=["total_liabilities"] if st_f8 == "fail" else None,
                        reason=exp_f8 if st_f8 == "fail" else None,
                        derived_L=derived_L,
                    ),
                )
            )

    return results


def _aggregate_decisions(
    bs: BalanceSheetInput,
    all_results: List[CheckResult],
    two_year_interp: str,
    cfg: ValidationConfig,
) -> Dict[str, MetricDecision]:
    """Aggregate metric decisions across checks & periods using Section 9/10 rules."""
    cur_period = bs.current
    agg: Dict[str, MetricDecision] = {}

    for m in CORE_METRICS:
        base = float(cur_period.base_confidences.get(m, cfg.default_base_confidence)) if cur_period else cfg.default_base_confidence
        pass_hits = 0
        warn_hits = 0
        hard_fail_reasons: List[str] = []
        blame_penalty = 0.0
        derived_penalty = 0.0

        for r in all_results:
            md = r.metric_decisions.get(m)
            if not md:
                continue
            if r.status == "fail":
                # Only penalize if this check actually penalized/escalated the metric (not cleared)
                if md.decision == "escalate" or (md.confidence_delta is not None and md.confidence_delta < 0):
                    delta = md.confidence_delta if md.confidence_delta is not None else cfg.fail_ambiguous_delta
                    blame_penalty = min(blame_penalty, delta)
                    if md.reason:
                        hard_fail_reasons.append(md.reason)
            elif r.status == "warn":
                if r.derived and m == "total_liabilities":
                    derived_penalty = min(derived_penalty, cfg.derived_delta)
                else:
                    warn_hits = max(warn_hits, 1)
            elif r.status == "pass":
                # Apply +0.05 when PASS touched this metric (do not erase an active fail)
                if md.confidence_delta and md.confidence_delta > 0:
                    pass_hits = 1

        # Section 9 & Example I: both periods fail -> systematic problem -> escalate all three!
        systematic_fail = two_year_interp == "systematic_problem"
        if systematic_fail:
            hard_fail_reasons.append("systematic problem across both current and prior periods")
            blame_penalty = min(blame_penalty, cfg.fail_ambiguous_delta)

        if hard_fail_reasons:
            # Section 10: a blamed metric drops cleanly by fail delta (no pass inflation on failed metric)
            net_delta = blame_penalty + derived_penalty
            conf = min(cfg.confidence_cap, max(cfg.confidence_floor, base + net_delta))
            decision = "escalate"
            reason = "; ".join(dict.fromkeys(hard_fail_reasons))
        else:
            net_delta = (cfg.pass_delta if pass_hits else 0.0) + (cfg.warn_delta if warn_hits else 0.0) + derived_penalty
            conf = min(cfg.confidence_cap, max(cfg.confidence_floor, base + net_delta))
            if conf < cfg.escalation_threshold:
                decision = "escalate"
                reason = f"confidence {conf:.2f} below threshold {cfg.escalation_threshold:.2f}"
            else:
                decision = "auto_approve"
                reason = None

        agg[m] = MetricDecision(
            confidence=round(conf, 4),
            decision=decision,
            reason=reason,
            base_confidence=base,
            confidence_delta=round(conf - base, 4),
        )

    # Also update each primary CheckResult's metric_decisions if systematic escalation occurred
    if two_year_interp == "systematic_problem":
        for r in all_results:
            if r.check_id == "accounting_equation" and r.status == "fail":
                for m in CORE_METRICS:
                    r.metric_decisions[m] = MetricDecision(
                        confidence=agg[m].confidence,
                        decision="escalate",
                        reason=agg[m].reason,
                    )

    return agg


def _interpret_two_years(results: List[CheckResult]) -> str:
    cur_main = next((r for r in results if r.period_role == "current" and r.check_id == "accounting_equation"), None)
    pri_main = next((r for r in results if r.period_role == "prior" and r.check_id == "accounting_equation"), None)

    cur_st = cur_main.status if cur_main else "skipped"
    pri_st = pri_main.status if pri_main else None

    if pri_st is None:
        return {
            "pass": "clean",
            "warn": "derived_or_warning",
            "fail": "current_year_error",
            "skipped": "skipped",
        }.get(cur_st, cur_st)

    cur_ok = cur_st in ("pass", "warn")
    pri_ok = pri_st in ("pass", "warn")
    if cur_st == "skipped" or pri_st == "skipped":
        if cur_st == "fail":
            return "current_year_error"
        if pri_st == "fail":
            return "prior_year_error"
        return "incomplete_or_skipped"

    if cur_ok and pri_ok:
        return "clean"
    if not cur_ok and pri_ok:
        return "current_year_error"
    if cur_ok and not pri_ok:
        return "prior_year_error"
    return "systematic_problem"


def _persist_report(
    report: ValidationReport,
    cfg: ValidationConfig,
    persistence_handler: Optional[Callable[[Dict[str, Any]], Any]] = None,
    persist: bool = False,
) -> Optional[str]:
    payload = report.to_dict()
    if persistence_handler is not None:
        res = persistence_handler(payload)
        return str(res) if res is not None else "custom_persistence_handler"
    if persist:
        out_dir = Path(cfg.default_output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        yr = report.fiscal_year or "latest"
        out_path = out_dir / f"{report.cik}_{yr}.json"
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(out_path)
    return None


def validate_balance_sheet_input(
    bs_input: BalanceSheetInput,
    config: Optional[Any] = None,
    persistence_handler: Optional[Callable[[Dict[str, Any]], Any]] = None,
    persist: bool = False,
) -> ValidationReport:
    """Run full accounting-equation validation on an already-constructed BalanceSheetInput."""
    cfg = load_config(config)
    all_results: List[CheckResult] = []

    if bs_input.current is not None:
        # Ensure current.prior_values is populated if prior exists and prior_values wasn't set
        cur_copy = deepcopy(bs_input.current)
        if not cur_copy.prior_values and bs_input.prior is not None:
            p = bs_input.prior
            cur_copy.prior_values = {
                k: v
                for k, v in {
                    "total_assets": p.A,
                    "total_liabilities": p.L,
                    "equity": p.E,
                    "noncontrolling_interest": p.NCI,
                    "temporary_equity": p.TE,
                    "total_liabilities_and_equity": p.LE,
                }.items()
                if v is not None
            }
        all_results.extend(
            _validate_single_period(bs_input, cur_copy, "current", cfg, prior_raw=bs_input.prior)
        )

    if bs_input.prior is not None:
        all_results.extend(_validate_single_period(bs_input, bs_input.prior, "prior", cfg))

    two_year_interp = _interpret_two_years(all_results)
    agg_decisions = _aggregate_decisions(bs_input, all_results, two_year_interp, cfg)

    status_counts = {"pass": 0, "fail": 0, "warn": 0, "skipped": 0}
    for r in all_results:
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    escalated = [m for m, d in agg_decisions.items() if d.decision == "escalate"]
    auto_approved = [m for m, d in agg_decisions.items() if d.decision == "auto_approve"]

    summary: Dict[str, Any] = {
        "counts": status_counts,
        "total_checks": len(all_results),
        "metrics_escalated": escalated,
        "metrics_auto_approved": auto_approved,
        "two_year_interpretation": two_year_interp,
    }

    report = ValidationReport(
        cik=bs_input.cik,
        company=bs_input.company,
        accession=bs_input.accession,
        fiscal_year=bs_input.fiscal_year,
        form=bs_input.form,
        results=all_results,
        two_year_interpretation=two_year_interp,
        summary=summary,
        metric_decisions=agg_decisions,
        missing_extractor_fields=list(bs_input.missing_extractor_fields),
    )
    report.persisted_to = _persist_report(report, cfg, persistence_handler=persistence_handler, persist=persist)
    return report


def validate_accounting_equation(
    cik: str,
    fiscal_year: Optional[int] = None,
    accession: Optional[str] = None,
    config: Optional[Any] = None,
    persistence_handler: Optional[Callable[[Dict[str, Any]], Any]] = None,
    persist: bool = False,
) -> Dict[str, Any]:
    """Primary engine entry point: load balance sheet for CIK and return Section 11 structured dict."""
    bs_input = load_balance_sheet(cik=cik, fiscal_year=fiscal_year, accession=accession)
    report = validate_balance_sheet_input(
        bs_input,
        config=config,
        persistence_handler=persistence_handler,
        persist=persist,
    )
    return report.to_dict()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic SEC balance-sheet accounting equation checks.")
    parser.add_argument("--cik", required=True, help="Company CIK (e.g. 320193 or 0000320193)")
    parser.add_argument("--year", type=int, default=None, help="Optional fiscal year (e.g. 2023)")
    parser.add_argument("--accession", default=None, help="Optional SEC accession number")
    parser.add_argument("--config", default=None, help="Optional YAML config path")
    parser.add_argument("--persist", action="store_true", help="Write JSON audit result to output dir")
    args = parser.parse_args(argv)

    result = validate_accounting_equation(
        cik=args.cik,
        fiscal_year=args.year,
        accession=args.accession,
        config=args.config,
        persist=args.persist,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
