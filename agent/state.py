"""Typed data state representation for the V.A.U.L.T workflow engine."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class AgentWorkflowState(TypedDict, total=False):
    run_id: str
    cik: str
    fiscal_year: int
    company: str
    status: str  # "RUNNING" | "WAITING_FOR_REVIEW" | "COMPLETED" | "FAILED"
    inject_error: Optional[str]
    filing: Dict[str, Any]
    metrics: List[Dict[str, Any]]
    checks: List[Dict[str, Any]]
    decisions: Dict[str, Dict[str, Any]]
    accuracy_report: Optional[Dict[str, Any]]
    completed_steps: List[str]
    error: Optional[str]
    created_at: str
    updated_at: str
