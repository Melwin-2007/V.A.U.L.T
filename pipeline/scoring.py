"""4-factor confidence scoring engine for financial metrics."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from data_verify.source_grounding import verify_source_grounding
from segmentation.confidence import compute_confidence, format_scaled_number, score_yoy_stability

WEIGHT_GROUNDING = 0.40
WEIGHT_LABEL = 0.25
WEIGHT_UNIT = 0.20
WEIGHT_YOY = 0.15


def check_multiscale_grounding(raw_val: float, snippet: str) -> float:
    """Verify verbatim digit presence in snippet across standard financial scales (M, K, B, 1)."""
    if not snippet or snippet == "Metric not found in filing XBRL facts":
        return 0.0

    # Test scales: 1M (most common in 10-K), 1K, 1B, and 1.0 (exact USD or EPS)
    for scale in [1e6, 1e3, 1e9, 1.0]:
        try:
            disp_str, scaled_num = format_scaled_number(raw_val, scale)
            g_res = verify_source_grounding(disp_str, snippet, numeric_value=scaled_num)
            if g_res.passed:
                return 1.0
            if scale == 1.0:
                # Also test two-decimal currency formats (e.g. $110,543,000,000.00)
                dec_res = verify_source_grounding(f"{raw_val:,.2f}", snippet, numeric_value=raw_val)
                if dec_res.passed:
                    return 1.0
        except Exception:
            continue

    return 0.0


def score_metrics(
    records: List[Dict[str, Any]],
    config: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Score confidence for all extracted metrics using 4-factor formula.

    Score = 0.40 * Grounding + 0.25 * Label + 0.20 * Unit + 0.15 * YoY
    """
    threshold = 0.85
    auto_approve_enabled = True
    if config:
        threshold = float(config.get("confidence_threshold", 0.85))
        auto_approve_enabled = bool(config.get("auto_approve_enabled", True))

    scored_records: List[Dict[str, Any]] = []

    for rec in records:
        r = copy.deepcopy(rec)
        val = r.get("value")
        snippet = r.get("source_snippet") or ""

        # Factor 1: Grounding
        if val is None or not snippet or snippet == "Metric not found in filing XBRL facts":
            s_grounding = 0.0
        else:
            s_grounding = check_multiscale_grounding(float(val), snippet)

        # Factor 2: Label
        if r.get("is_derived"):
            s_label = 0.85
        elif r.get("xbrl_tag") and "us-gaap:" in r["xbrl_tag"]:
            s_label = 1.0
        elif r.get("xbrl_tag"):
            s_label = 0.80
        else:
            s_label = 0.50

        # Factor 3: Unit
        unit = r.get("unit")
        if unit in ("USD", "USD/shares", "shares"):
            s_unit = 1.0
        elif unit:
            s_unit = 0.70
        else:
            s_unit = 0.30

        # Factor 4: YoY Stability
        prior_val = r.get("prior_value")
        if val is not None and prior_val is not None:
            s_yoy, _ = score_yoy_stability(float(val), float(prior_val))
        else:
            s_yoy = 1.0  # Default neutral for single-period or unavailable prior

        # If metric had injected error altering factor breakdown
        if r.get("injected_error"):
            existing_bd = r.get("confidence_breakdown") or {}
            if existing_bd.get("grounding") == 0.0:
                s_grounding = 0.0
            if existing_bd.get("yoy") == 0.3:
                s_yoy = 0.3

        cb = compute_confidence(
            s_grounding=s_grounding,
            s_label=s_label,
            s_unit=s_unit,
            s_yoy=s_yoy,
        )

        r["confidence"] = cb.score
        r["confidence_breakdown"] = {
            "grounding": cb.s_grounding,
            "label": cb.s_label,
            "unit": cb.s_unit,
            "yoy": cb.s_yoy,
        }

        # Auto-approval evaluation (Trigger A check)
        if cb.score >= threshold and auto_approve_enabled:
            r["status"] = "AUTO_APPROVED"
            r["escalation_reason"] = None
        else:
            r["status"] = "NEEDS_REVIEW"
            r["escalation_reason"] = (
                f"Low confidence score ({cb.score:.2f} < {threshold:.2f}) [Trigger A]"
            )

        scored_records.append(r)

    return scored_records
