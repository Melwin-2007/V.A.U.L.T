"""FastAPI routes for Year-on-Year validation, human review, and audit trails."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.yoy_models import (
    AuditTrailEntry,
    HumanReviewRequest,
    ValidationResult,
    YoYValidationRequest,
    YoYValidationResponse,
)
from app.services.audit_logger import AuditStore
from app.services.yoy_validator import apply_human_review, validate_yoy

DEFAULT_AUDIT_LOG = Path("audit_logs/yoy_audit_trail.jsonl")
_default_store = AuditStore(log_file=DEFAULT_AUDIT_LOG)


def get_audit_store() -> AuditStore:
    """Dependency provider for the audit store (overridable in tests)."""
    return _default_store


router = APIRouter(prefix="/api/v1/yoy", tags=["yoy-validation"])


@router.post("/validate", response_model=YoYValidationResponse)
def validate_yoy_endpoint(
    payload: YoYValidationRequest,
    store: AuditStore = Depends(get_audit_store),
) -> YoYValidationResponse:
    """Validate Year-on-Year financial change and record initial audit entry."""
    result = validate_yoy(payload)
    store.save_validation(result)
    return result


@router.post("/review", response_model=YoYValidationResponse)
def submit_human_review(
    review: HumanReviewRequest,
    store: AuditStore = Depends(get_audit_store),
) -> YoYValidationResponse:
    """Record a human reviewer's APPROVE / CORRECT / REJECT decision."""
    existing = store.get_validation(review.validation_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Validation record {review.validation_id!r} was not found.",
        )
    if existing.validation_result == ValidationResult.CANNOT_VALIDATE:
        raise HTTPException(
            status_code=400,
            detail="Cannot submit human review for a record with status CANNOT_VALIDATE.",
        )

    updated = apply_human_review(existing, review)
    store.record_review(updated, review)
    return updated


@router.get("/validations/{validation_id}", response_model=YoYValidationResponse)
def get_validation_record(
    validation_id: str,
    store: AuditStore = Depends(get_audit_store),
) -> YoYValidationResponse:
    """Retrieve the current state of a YoY validation record."""
    record = store.get_validation(validation_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Validation record {validation_id!r} was not found.",
        )
    return record


@router.get("/audit-trail", response_model=list[AuditTrailEntry])
def list_audit_trail(
    validation_id: Optional[str] = Query(default=None),
    store: AuditStore = Depends(get_audit_store),
) -> list[AuditTrailEntry]:
    """List immutable audit trail entries, optionally filtered by validation_id."""
    return store.list_audit_entries(validation_id=validation_id)
