"""Pydantic models and enums for the Year-on-Year Financial Validation Agent."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


def _coerce_decimal_or_none(value: Any) -> Optional[Decimal]:
    """Convert numeric inputs cleanly to exact Decimal without float drift."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid numeric value.")
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return Decimal(str(value))
    if isinstance(value, str):
        stripped = value.strip().replace(",", "")
        if not stripped:
            return None
        try:
            parsed = Decimal(stripped)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid numeric value: {value!r}") from exc
        if not parsed.is_finite():
            return None
        return parsed
    raise ValueError(f"Unsupported numeric type: {type(value).__name__}")


class ValidationResult(str, Enum):
    """Deterministic YoY validation outcomes."""

    PASS = "PASS"
    UNUSUAL = "UNUSUAL"
    CANNOT_VALIDATE = "CANNOT_VALIDATE"


class HumanAction(str, Enum):
    """Actions available to a human reviewer."""

    APPROVE = "APPROVE"
    CORRECT = "CORRECT"
    REJECT = "REJECT"


class HumanDecision(str, Enum):
    """Recorded human review decisions."""

    APPROVED = "APPROVED"
    CORRECTED = "CORRECTED"
    REJECTED = "REJECTED"


class FinalStatus(str, Enum):
    """Lifecycle status combining automated validation and human review."""

    PASSED = "PASSED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
    CANNOT_VALIDATE = "CANNOT_VALIDATE"
    HUMAN_VERIFIED = "HUMAN_VERIFIED"
    HUMAN_CORRECTED = "HUMAN_CORRECTED"
    HUMAN_REJECTED = "HUMAN_REJECTED"


class YearMetricInput(BaseModel):
    """Financial metric observation for a single reporting year."""

    year: Optional[int] = None
    value: Optional[Decimal] = None
    unit: Optional[str] = None
    metric: Optional[str] = None

    @field_validator("value", mode="before")
    @classmethod
    def _parse_value(cls, value: Any) -> Optional[Decimal]:
        return _coerce_decimal_or_none(value)


class YoYValidationRequest(BaseModel):
    """Structured input for Year-on-Year validation."""

    company: Optional[str] = None
    metric: Optional[str] = None
    current_year: Optional[YearMetricInput] = None
    previous_year: Optional[YearMetricInput] = None
    threshold_percent: Decimal = Field(default=Decimal("30"), ge=Decimal("0"))
    automated_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("threshold_percent", mode="before")
    @classmethod
    def _parse_threshold(cls, value: Any) -> Decimal:
        if value is None:
            return Decimal("30")
        parsed = _coerce_decimal_or_none(value)
        if parsed is None:
            raise ValueError("threshold_percent must be a finite number.")
        return parsed


class UnitNormalizationRecord(BaseModel):
    """Explicit record of unit normalization applied to a single year's value."""

    original_unit: Optional[str] = None
    normalized_unit: Optional[str] = None
    conversion_applied: bool = False
    original_value: Optional[float] = None
    normalized_value: Optional[float] = None


class UnitNormalizationSummary(BaseModel):
    """Unit normalization details for both reporting years."""

    current_year: UnitNormalizationRecord
    previous_year: UnitNormalizationRecord


class YoYValidationResponse(BaseModel):
    """Structured output returned by the YoY Financial Validation Agent."""

    validation_id: str
    company: Optional[str] = None
    metric: Optional[str] = None
    current_year: Optional[int] = None
    current_value: Optional[float] = None
    previous_year: Optional[int] = None
    previous_value: Optional[float] = None
    unit: Optional[str] = None
    unit_normalization: Optional[UnitNormalizationSummary] = None

    yoy_percent: Optional[float] = None
    threshold_percent: float

    validation_result: ValidationResult
    status: ValidationResult
    human_review_required: bool

    reason: str
    small_denominator_warning: bool = False

    automated_confidence: Optional[float] = None

    human_decision: Optional[HumanDecision] = None
    human_verified: bool = False
    final_status: FinalStatus

    original_value: Optional[float] = None
    corrected_value: Optional[float] = None


class HumanReviewRequest(BaseModel):
    """Reviewer decision payload for an evaluated YoY record."""

    validation_id: str
    action: HumanAction
    corrected_value: Optional[Decimal] = None
    reviewer: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("corrected_value", mode="before")
    @classmethod
    def _parse_corrected_value(cls, value: Any) -> Optional[Decimal]:
        return _coerce_decimal_or_none(value)

    @model_validator(mode="after")
    def _validate_correction_payload(self) -> "HumanReviewRequest":
        if self.action == HumanAction.CORRECT and self.corrected_value is None:
            raise ValueError("corrected_value is required when action is CORRECT.")
        return self


class AuditTrailEntry(BaseModel):
    """Immutable audit record preserving automated extraction and human decision."""

    audit_id: str
    validation_id: str
    company: Optional[str] = None
    metric: Optional[str] = None
    current_year: Optional[int] = None
    previous_year: Optional[int] = None
    previous_value: Optional[float] = None
    current_value: Optional[float] = None
    unit: Optional[str] = None
    yoy_percent: Optional[float] = None
    threshold_percent: float
    validation_result: ValidationResult
    automated_confidence: Optional[float] = None
    human_decision: Optional[HumanDecision] = None
    human_verified: bool = False
    final_status: FinalStatus
    timestamp: str
    original_value: Optional[float] = None
    corrected_value: Optional[float] = None
    reviewer: Optional[str] = None
    notes: Optional[str] = None
