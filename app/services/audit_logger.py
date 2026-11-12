"""Audit trail storage and state repository for YoY validations and reviews."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional
from uuid import uuid4

from app.models.yoy_models import (
    AuditTrailEntry,
    HumanReviewRequest,
    YoYValidationResponse,
)


class AuditStore:
    """Thread-safe store for YoY validations and immutable review audit logs."""

    def __init__(self, log_file: Optional[Path] = None) -> None:
        self._lock = Lock()
        self._validations: dict[str, YoYValidationResponse] = {}
        self._audit_entries: list[AuditTrailEntry] = []
        self._log_file = log_file

    def save_validation(self, response: YoYValidationResponse) -> YoYValidationResponse:
        """Persist a validation result and record an initial audit entry."""
        with self._lock:
            self._validations[response.validation_id] = response
            entry = self._build_entry(response)
            self._audit_entries.append(entry)
            self._append_to_file(entry)
        return response

    def get_validation(self, validation_id: str) -> Optional[YoYValidationResponse]:
        """Look up a previously evaluated validation by ID."""
        with self._lock:
            return self._validations.get(validation_id)

    def record_review(
        self,
        updated: YoYValidationResponse,
        review: HumanReviewRequest,
    ) -> AuditTrailEntry:
        """Update stored validation state and append an immutable review audit entry."""
        with self._lock:
            self._validations[updated.validation_id] = updated
            entry = self._build_entry(
                updated,
                reviewer=review.reviewer,
                notes=review.notes,
            )
            self._audit_entries.append(entry)
            self._append_to_file(entry)
        return entry

    def list_audit_entries(
        self, validation_id: Optional[str] = None
    ) -> list[AuditTrailEntry]:
        """Return recorded audit trail entries, optionally filtered by validation_id."""
        with self._lock:
            if validation_id is None:
                return list(self._audit_entries)
            return [
                entry
                for entry in self._audit_entries
                if entry.validation_id == validation_id
            ]

    def clear(self) -> None:
        """Reset in-memory state (used by test fixtures)."""
        with self._lock:
            self._validations.clear()
            self._audit_entries.clear()

    @staticmethod
    def _build_entry(
        response: YoYValidationResponse,
        reviewer: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> AuditTrailEntry:
        return AuditTrailEntry(
            audit_id=str(uuid4()),
            validation_id=response.validation_id,
            company=response.company,
            metric=response.metric,
            current_year=response.current_year,
            previous_year=response.previous_year,
            previous_value=response.previous_value,
            current_value=response.current_value,
            unit=response.unit,
            yoy_percent=response.yoy_percent,
            threshold_percent=response.threshold_percent,
            validation_result=response.validation_result,
            automated_confidence=response.automated_confidence,
            human_decision=response.human_decision,
            human_verified=response.human_verified,
            final_status=response.final_status,
            timestamp=datetime.now(timezone.utc).isoformat(),
            original_value=response.original_value,
            corrected_value=response.corrected_value,
            reviewer=reviewer,
            notes=notes,
        )

    def _append_to_file(self, entry: AuditTrailEntry) -> None:
        if self._log_file is None:
            return
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        with self._log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.model_dump(mode="json")) + "\n")
