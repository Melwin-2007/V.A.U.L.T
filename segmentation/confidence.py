from __future__ import annotations

import re
from typing import Iterable

from data_verify.source_grounding import normalize_snippet, verify_source_grounding
from segmentation.models import (
    ConfidenceBreakdown,
    MetricValidation,
    ReviewDecision,
    UnitContext,
)

WEIGHT_GROUNDING = 0.40
WEIGHT_LABEL = 0.25
WEIGHT_UNIT = 0.20
WEIGHT_YOY = 0.15

DEFAULT_CONFIDENCE_THRESHOLD = 0.85
DEFAULT_YOY_THRESHOLD = 0.25

_MILLIONS_PATTERN = re.compile(
    r"\b(?:in\s+millions|\$\s*in\s+millions|amounts?\s+in\s+millions|millions)\b",
    re.IGNORECASE,
)
_THOUSANDS_PATTERN = re.compile(
    r"\b(?:in\s+thousands|\$\s*in\s+thousands|amounts?\s+in\s+thousands|thousands)\b",
    re.IGNORECASE,
)
_BILLIONS_PATTERN = re.compile(
    r"\b(?:in\s+billions|\$\s*in\s+billions|amounts?\s+in\s+billions|billions)\b",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b(20\d{2}|19\d{2})\b")


def compute_confidence(
    *,
    s_grounding: float,
    s_label: float,
    s_unit: float,
    s_yoy: float,
) -> ConfidenceBreakdown:
    """Compute weighted confidence score in [0.00, 1.00]."""
    raw_score = (
        (WEIGHT_GROUNDING * s_grounding)
        + (WEIGHT_LABEL * s_label)
        + (WEIGHT_UNIT * s_unit)
        + (WEIGHT_YOY * s_yoy)
    )
    clamped = max(0.0, min(1.0, round(raw_score, 4)))
    return ConfidenceBreakdown(
        s_grounding=round(s_grounding, 4),
        s_label=round(s_label, 4),
        s_unit=round(s_unit, 4),
        s_yoy=round(s_yoy, 4),
        score=clamped,
    )


def score_yoy_stability(
    current_value: float,
    prior_value: float | None,
    *,
    max_normal_change: float = DEFAULT_YOY_THRESHOLD,
) -> tuple[float, float | None]:
    """Score YoY stability: 1.0 if |YoY| <= 25%, 0.3 if > 25%, 1.0 if prior unavailable/zero."""
    if prior_value is None or prior_value == 0:
        if prior_value == 0 and current_value != 0:
            return 0.3, None
        return 1.0, None

    pct_change = (current_value - prior_value) / abs(prior_value)
    score = 1.0 if abs(pct_change) <= max_normal_change else 0.3
    return score, round(pct_change * 100.0, 2)


def detect_table_unit_and_period(
    header_texts: Iterable[str],
    *,
    expected_year: str | None = None,
    fallback_scale_divisor: float = 1e6,
) -> UnitContext:
    """Detect explicit fiscal year and unit scale from table headers/captions."""
    joined = normalize_snippet(" | ".join(t for t in header_texts if t))
    years = _YEAR_PATTERN.findall(joined)
    fiscal_detected = bool(years) and (
        expected_year is None or expected_year in years or len(years) > 0
    )

    if _MILLIONS_PATTERN.search(joined):
        return UnitContext(
            unit_label="USD millions",
            scale_divisor=1e6,
            fiscal_period_detected=fiscal_detected,
            unit_scale_detected=True,
            fiscal_label=expected_year or (years[0] if years else ""),
            header_snippet=joined[:300],
        )
    if _THOUSANDS_PATTERN.search(joined):
        return UnitContext(
            unit_label="USD thousands",
            scale_divisor=1e3,
            fiscal_period_detected=fiscal_detected,
            unit_scale_detected=True,
            fiscal_label=expected_year or (years[0] if years else ""),
            header_snippet=joined[:300],
        )
    if _BILLIONS_PATTERN.search(joined):
        return UnitContext(
            unit_label="USD billions",
            scale_divisor=1e9,
            fiscal_period_detected=fiscal_detected,
            unit_scale_detected=True,
            fiscal_label=expected_year or (years[0] if years else ""),
            header_snippet=joined[:300],
        )

    fallback_label = (
        "USD millions"
        if fallback_scale_divisor == 1e6
        else "USD thousands"
        if fallback_scale_divisor == 1e3
        else "USD"
    )
    return UnitContext(
        unit_label=fallback_label,
        scale_divisor=fallback_scale_divisor,
        fiscal_period_detected=fiscal_detected,
        unit_scale_detected=False,
        fiscal_label=expected_year or (years[0] if years else ""),
        header_snippet=joined[:300],
    )


def score_unit_clarity(unit_ctx: UnitContext) -> float:
    """Return 1.0 when both period and explicit unit scale appear in table header, else 0.5."""
    if unit_ctx.fiscal_period_detected and unit_ctx.unit_scale_detected:
        return 1.0
    return 0.5


def format_scaled_number(raw_value: float, scale_divisor: float) -> tuple[str, float | int]:
    """Format a raw USD value into the table's display unit for verbatim matching."""
    scaled = raw_value / scale_divisor
    rounded = round(scaled)
    if abs(scaled - rounded) < 1e-6:
        return f"{rounded:,}", rounded
    if abs(scaled * 10 - round(scaled * 10)) < 1e-4:
        val = round(scaled, 1)
        return f"{val:,.1f}", val
    val = round(scaled, 2)
    return f"{val:,.2f}", val


_ZERO_CELL_PATTERN = re.compile(
    r"(?:\$\s*[—–\-](?!\s*\d)|(?:^|\|\s*)[—–\-](?:\s*\||$))"
)


def has_accounting_zero_marker(text: str) -> bool:
    """Detect standard SEC accounting zero placeholders (e.g. '—', '$—', '| - |')."""
    return _ZERO_CELL_PATTERN.search(normalize_snippet(text)) is not None


def verify_metric_grounding(
    raw_value: float,
    source_snippet: str,
    *,
    scale_divisor: float,
    is_derived: bool = False,
) -> tuple[bool, bool, str | None, str, float | int]:
    """Verify exact verbatim digits in source_snippet; derived rows never pass verbatim grounding."""
    display_val, numeric_scaled = format_scaled_number(raw_value, scale_divisor)
    if is_derived:
        return False, True, None, display_val, numeric_scaled

    res = verify_source_grounding(display_val, source_snippet, numeric_value=numeric_scaled)
    if res.passed:
        return True, False, res.matched_text, display_val, numeric_scaled

    # Also check unscaled raw USD digits in case a table reports exact dollars despite header guess.
    if scale_divisor != 1.0:
        alt_display, alt_num = format_scaled_number(raw_value, 1.0)
        alt_res = verify_source_grounding(alt_display, source_snippet, numeric_value=alt_num)
        if alt_res.passed:
            return True, False, alt_res.matched_text, alt_display, alt_num

    # SEC tables commonly display a zero cell as '—' or '$—' (e.g. Corporate revenue = $—).
    if raw_value == 0.0:
        norm = normalize_snippet(source_snippet)
        zero_match = _ZERO_CELL_PATTERN.search(norm)
        if zero_match:
            return True, False, zero_match.group(0).strip(" |"), display_val, numeric_scaled

    return False, True, None, display_val, numeric_scaled


def validate_metric(
    *,
    metric: str,
    label: str,
    raw_value: float,
    prior_value: float | None,
    period: str,
    source_section: str,
    source_snippet: str,
    unit_ctx: UnitContext,
    s_label: float = 1.0,
    is_derived: bool = False,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    yoy_threshold: float = DEFAULT_YOY_THRESHOLD,
    extra_escalation_reasons: list[str] | None = None,
) -> MetricValidation:
    """Evaluate grounding, weighted confidence, and Trigger A/B human-review escalation."""
    passed_grounding, hallucination_flag, matched_text, display_val, _ = verify_metric_grounding(
        raw_value,
        source_snippet,
        scale_divisor=unit_ctx.scale_divisor,
        is_derived=is_derived,
    )
    s_grounding = 1.0 if passed_grounding else 0.0
    s_unit = score_unit_clarity(unit_ctx)
    s_yoy, yoy_pct = score_yoy_stability(
        raw_value, prior_value, max_normal_change=yoy_threshold
    )

    confidence = compute_confidence(
        s_grounding=s_grounding,
        s_label=s_label,
        s_unit=s_unit,
        s_yoy=s_yoy,
    )

    reasons: list[str] = list(extra_escalation_reasons or [])
    if is_derived:
        reasons.append(
            f"Low Confidence ({confidence.score:.2f}): Value derived from multi-row addition; "
            "not found verbatim in source snippet."
        )
    elif not passed_grounding:
        reasons.append(
            f"Source Grounding Failed: Exact digits '{display_val}' not found verbatim in source snippet."
        )

    if confidence.score < confidence_threshold and not is_derived:
        reasons.append(
            f"Low Confidence ({confidence.score:.2f} < {confidence_threshold:.2f})."
        )

    decision: ReviewDecision = "HUMAN_REVIEW" if reasons else "AUTO-APPROVED"
    return MetricValidation(
        metric=metric,
        label=label,
        value=raw_value,
        prior_value=prior_value,
        display_value=display_val,
        unit=unit_ctx.unit_label,
        period=period,
        yoy_change_pct=yoy_pct,
        source_section=source_section,
        source_snippet=source_snippet,
        grounding_passed=passed_grounding,
        hallucination_flag=hallucination_flag,
        matched_text=matched_text,
        confidence=confidence,
        decision=decision,
        escalation_reasons=reasons,
    )


def escalate_metric_for_accounting_rule(
    metric_val: MetricValidation,
    rule_reason: str,
) -> MetricValidation:
    """Trigger B: force HUMAN_REVIEW on an otherwise high-confidence metric."""
    updated_reasons = [*metric_val.escalation_reasons, rule_reason]
    return MetricValidation(
        metric=metric_val.metric,
        label=metric_val.label,
        value=metric_val.value,
        prior_value=metric_val.prior_value,
        display_value=metric_val.display_value,
        unit=metric_val.unit,
        period=metric_val.period,
        yoy_change_pct=metric_val.yoy_change_pct,
        source_section=metric_val.source_section,
        source_snippet=metric_val.source_snippet,
        grounding_passed=metric_val.grounding_passed,
        hallucination_flag=metric_val.hallucination_flag,
        matched_text=metric_val.matched_text,
        confidence=metric_val.confidence,
        decision="HUMAN_REVIEW",
        escalation_reasons=updated_reasons,
    )

