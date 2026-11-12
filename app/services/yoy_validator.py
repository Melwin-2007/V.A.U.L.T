"""Pure deterministic Year-on-Year (YoY) financial validation engine."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import uuid4

from app.models.yoy_models import (
    FinalStatus,
    HumanAction,
    HumanDecision,
    HumanReviewRequest,
    UnitNormalizationRecord,
    UnitNormalizationSummary,
    ValidationResult,
    YoYValidationRequest,
    YoYValidationResponse,
)
from app.services.unit_normalizer import UnitNormalizationError, normalize_pair

SMALL_DENOMINATOR_LIMIT = Decimal("1")


def calculate_yoy_percent(current_value: Decimal, previous_value: Decimal) -> Decimal:
    """Calculate exact Year-on-Year percentage change.

    Formula: ((Current Year Value - Previous Year Value) / Previous Year Value) * 100
    Preserves sign and raises ValueError when previous_value is zero.
    """
    if previous_value == Decimal("0"):
        raise ValueError(
            "YoY percentage cannot be calculated because the previous-year value is zero."
        )
    return ((current_value - previous_value) / previous_value) * Decimal("100")


def _format_signed_percent(yoy: Decimal) -> str:
    """Format a YoY percentage with explicit sign for human-readable explanations."""
    rounded = yoy.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Keep higher precision if rounding to 2 decimals would hide a small exceedance
    if rounded == yoy.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP):
        display_val = yoy.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        display_val = yoy.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP).normalize()
    sign = "+" if display_val > 0 else ""
    return f"{sign}{display_val}%"


def _round_output_percent(yoy: Decimal) -> float:
    """Convert exact Decimal YoY percentage to clean float for JSON serialization."""
    return float(yoy.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _cannot_validate_response(
    request: YoYValidationRequest,
    reason: str,
    validation_id: str,
    unit_summary: Optional[UnitNormalizationSummary] = None,
) -> YoYValidationResponse:
    curr_year = request.current_year.year if request.current_year else None
    prev_year = request.previous_year.year if request.previous_year else None
    curr_val = (
        float(request.current_year.value)
        if request.current_year and request.current_year.value is not None
        else None
    )
    prev_val = (
        float(request.previous_year.value)
        if request.previous_year and request.previous_year.value is not None
        else None
    )
    return YoYValidationResponse(
        validation_id=validation_id,
        company=request.company,
        metric=request.metric,
        current_year=curr_year,
        current_value=curr_val,
        previous_year=prev_year,
        previous_value=prev_val,
        unit=None,
        unit_normalization=unit_summary,
        yoy_percent=None,
        threshold_percent=float(request.threshold_percent),
        validation_result=ValidationResult.CANNOT_VALIDATE,
        status=ValidationResult.CANNOT_VALIDATE,
        human_review_required=False,
        reason=reason,
        small_denominator_warning=False,
        automated_confidence=request.automated_confidence,
        human_decision=None,
        human_verified=False,
        final_status=FinalStatus.CANNOT_VALIDATE,
        original_value=curr_val,
        corrected_value=None,
    )


def _validate_inputs(request: YoYValidationRequest) -> Optional[str]:
    """Return a clear error reason if required inputs are missing or invalid."""
    if request.current_year is None:
        return "Current-year data is missing."
    if request.previous_year is None:
        return "Previous-year data is missing."

    if request.current_year.year is None:
        return "Current reporting year is missing."
    if request.previous_year.year is None:
        return "Previous reporting year is missing."

    if request.current_year.year <= request.previous_year.year:
        return (
            f"Current year ({request.current_year.year}) must be later than "
            f"previous year ({request.previous_year.year})."
        )

    if request.current_year.value is None:
        return "Current-year financial value is missing or non-finite."
    if request.previous_year.value is None:
        return "Previous-year financial value is missing or non-finite."

    if not request.current_year.unit or not request.current_year.unit.strip():
        return "Current-year unit is missing."
    if not request.previous_year.unit or not request.previous_year.unit.strip():
        return "Previous-year unit is missing."

    # Check metric consistency if individual year metrics are supplied.
    curr_metric = (request.current_year.metric or "").strip()
    prev_metric = (request.previous_year.metric or "").strip()
    top_metric = (request.metric or "").strip()

    if curr_metric and prev_metric and curr_metric.lower() != prev_metric.lower():
        return (
            f"Metric mismatch: current year metric ({curr_metric!r}) does not match "
            f"previous year metric ({prev_metric!r})."
        )
    if top_metric and curr_metric and curr_metric.lower() != top_metric.lower():
        return (
            f"Metric mismatch: current year metric ({curr_metric!r}) does not match "
            f"requested metric ({top_metric!r})."
        )
    if top_metric and prev_metric and prev_metric.lower() != top_metric.lower():
        return (
            f"Metric mismatch: previous year metric ({prev_metric!r}) does not match "
            f"requested metric ({top_metric!r})."
        )

    return None


def _build_reason(
    metric_label: str,
    yoy_exact: Decimal,
    threshold: Decimal,
    is_unusual: bool,
    norm_prev: Decimal,
    norm_curr: Decimal,
    norm_unit: str,
    prev_year: int,
    curr_year: int,
    small_denominator: bool,
) -> str:
    """Build a neutral, audit-ready explanation without claiming values are wrong."""
    pct_str = _format_signed_percent(yoy_exact)
    threshold_str = f"{threshold.normalize():f}%"
    if yoy_exact > 0:
        direction = f"increased by {pct_str}"
    elif yoy_exact < 0:
        direction = f"decreased by {pct_str}"
    else:
        direction = "was unchanged (0.00%)"

    context = (
        f"({prev_year}: {norm_prev.normalize():f} {norm_unit} -> "
        f"{curr_year}: {norm_curr.normalize():f} {norm_unit})"
    )

    if is_unusual:
        base = (
            f"{metric_label} {direction} compared with the previous year {context}, "
            f"exceeding the configured {threshold_str} review threshold. "
            "This flags an unusual change for human review, not an error."
        )
    else:
        base = (
            f"{metric_label} {direction} compared with the previous year {context}, "
            f"which is within the configured {threshold_str} review threshold."
        )

    notes: list[str] = []
    if norm_prev < Decimal("0"):
        notes.append(
            "Note: previous-year value is negative, so the signed mathematical YoY formula is preserved with underlying values shown for context."
        )
    if small_denominator:
        notes.append(
            "Note: previous-year denominator is small (magnitude < 1), which can amplify percentage swings."
        )

    if notes:
        return f"{base} {' '.join(notes)}"
    return base


def validate_yoy(
    request: YoYValidationRequest,
    validation_id: Optional[str] = None,
) -> YoYValidationResponse:
    """Run full deterministic Year-on-Year validation on a structured request."""
    vid = validation_id or str(uuid4())

    # Phase 2: Input validation
    validation_error = _validate_inputs(request)
    if validation_error is not None:
        return _cannot_validate_response(request, validation_error, vid)

    assert request.current_year is not None
    assert request.previous_year is not None
    assert request.current_year.value is not None
    assert request.previous_year.value is not None
    assert request.current_year.year is not None
    assert request.previous_year.year is not None

    # Phase 3: Unit normalization
    try:
        norm_curr, norm_prev, norm_unit, unit_summary = normalize_pair(
            current_value=request.current_year.value,
            current_unit=request.current_year.unit,
            previous_value=request.previous_year.value,
            previous_unit=request.previous_year.unit,
        )
    except UnitNormalizationError as exc:
        partial_summary = UnitNormalizationSummary(
            current_year=UnitNormalizationRecord(
                original_unit=request.current_year.unit,
                normalized_unit=None,
                conversion_applied=False,
                original_value=float(request.current_year.value),
                normalized_value=None,
            ),
            previous_year=UnitNormalizationRecord(
                original_unit=request.previous_year.unit,
                normalized_unit=None,
                conversion_applied=False,
                original_value=float(request.previous_year.value),
                normalized_value=None,
            ),
        )
        return _cannot_validate_response(request, str(exc), vid, partial_summary)

    # Guard against zero previous-year denominator after normalization
    if norm_prev == Decimal("0"):
        return _cannot_validate_response(
            request,
            "YoY percentage cannot be calculated because the previous-year value is zero.",
            vid,
            unit_summary,
        )

    # Phase 1 & 4: Deterministic exact Decimal YoY calculation & threshold check
    yoy_exact = calculate_yoy_percent(norm_curr, norm_prev)
    is_unusual = abs(yoy_exact) > request.threshold_percent
    small_denominator = abs(norm_prev) < SMALL_DENOMINATOR_LIMIT

    result_status = ValidationResult.UNUSUAL if is_unusual else ValidationResult.PASS
    final_status = (
        FinalStatus.PENDING_HUMAN_REVIEW if is_unusual else FinalStatus.PASSED
    )

    metric_label = request.metric or "Metric"
    reason = _build_reason(
        metric_label=metric_label,
        yoy_exact=yoy_exact,
        threshold=request.threshold_percent,
        is_unusual=is_unusual,
        norm_prev=norm_prev,
        norm_curr=norm_curr,
        norm_unit=norm_unit,
        prev_year=request.previous_year.year,
        curr_year=request.current_year.year,
        small_denominator=small_denominator,
    )

    return YoYValidationResponse(
        validation_id=vid,
        company=request.company,
        metric=request.metric,
        current_year=request.current_year.year,
        current_value=float(norm_curr),
        previous_year=request.previous_year.year,
        previous_value=float(norm_prev),
        unit=norm_unit,
        unit_normalization=unit_summary,
        yoy_percent=_round_output_percent(yoy_exact),
        threshold_percent=float(request.threshold_percent),
        validation_result=result_status,
        status=result_status,
        human_review_required=is_unusual,
        reason=reason,
        small_denominator_warning=small_denominator,
        automated_confidence=request.automated_confidence,
        human_decision=None,
        human_verified=False,
        final_status=final_status,
        original_value=float(norm_curr),
        corrected_value=None,
    )


def apply_human_review(
    existing: YoYValidationResponse,
    review: HumanReviewRequest,
) -> YoYValidationResponse:
    """Apply a human reviewer decision without erasing original extraction or confidence."""
    if review.action == HumanAction.APPROVE:
        decision = HumanDecision.APPROVED
        verified = True
        final_status = FinalStatus.HUMAN_VERIFIED
        corrected_value = None
    elif review.action == HumanAction.CORRECT:
        decision = HumanDecision.CORRECTED
        verified = True
        final_status = FinalStatus.HUMAN_CORRECTED
        corrected_value = (
            float(review.corrected_value)
            if review.corrected_value is not None
            else None
        )
    else:
        decision = HumanDecision.REJECTED
        verified = False
        final_status = FinalStatus.HUMAN_REJECTED
        corrected_value = None

    return existing.model_copy(
        update={
            "human_decision": decision,
            "human_verified": verified,
            "final_status": final_status,
            "corrected_value": corrected_value,
        }
    )
