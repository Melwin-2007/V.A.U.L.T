"""FastAPI routes for V.A.U.L.T workflow runs, human review, audit logs, and configuration."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from agent.audit import get_audit_records, verify_chain
from agent.config import get_config, update_config
from agent.graph import (
    get_workflow_state,
    list_workflows,
    resume_workflow,
    start_workflow,
)

router = APIRouter(prefix="/api/v1", tags=["runs"])


class StartRunRequest(BaseModel):
    cik: str = Field(..., description="SEC Central Index Key (e.g. 0000320193)")
    fiscal_year: int = Field(2023, description="Fiscal year (e.g. 2023)")
    inject_error: Optional[str] = Field(
        None,
        description="Optional error injection: scale_1000x, sign_flip, wrong_column, missing_digit",
    )


class ReviewSubmissionRequest(BaseModel):
    metric_id: str = Field(..., description="Full metric ID (e.g. run_id:metric_name)")
    decision: str = Field(..., description="Review decision: approve, correct, or reject")
    corrected_value: Optional[float] = Field(None, description="Corrected value in full USD (if correct)")
    reviewer: str = Field("Human Auditor", description="Reviewer name or identifier")
    comment: str = Field("", description="Audit justification comment")


class ConfigUpdateRequest(BaseModel):
    confidence_threshold: Optional[float] = None
    auto_approve_enabled: Optional[bool] = None
    default_pct: Optional[float] = None
    warn_ratio: Optional[float] = None
    metrics: Optional[Dict[str, float]] = None


@router.post("/runs")
def create_run(req: StartRunRequest) -> Dict[str, Any]:
    """Start an extraction and validation workflow for a company CIK."""
    state = start_workflow(
        cik=req.cik,
        fiscal_year=req.fiscal_year,
        inject_error=req.inject_error,
    )
    metrics = state.get("metrics", [])
    counts = {
        "total": len(metrics),
        "auto_approved": sum(1 for m in metrics if m.get("status") == "AUTO_APPROVED"),
        "needs_review": sum(1 for m in metrics if m.get("status") == "NEEDS_REVIEW"),
        "approved": sum(1 for m in metrics if m.get("status") == "APPROVED"),
        "corrected": sum(1 for m in metrics if m.get("status") == "CORRECTED"),
        "rejected": sum(1 for m in metrics if m.get("status") == "REJECTED"),
    }
    return {
        "run_id": state["run_id"],
        "cik": state["cik"],
        "company": state["company"],
        "status": state["status"],
        "counts": counts,
        "created_at": state["created_at"],
    }


@router.get("/runs")
def list_runs() -> List[Dict[str, Any]]:
    """List all workflow runs."""
    return list_workflows()


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> Dict[str, Any]:
    """Retrieve full details of a specific workflow run."""
    state = get_workflow_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    metrics = state.get("metrics", [])
    counts = {
        "total": len(metrics),
        "auto_approved": sum(1 for m in metrics if m.get("status") == "AUTO_APPROVED"),
        "needs_review": sum(1 for m in metrics if m.get("status") == "NEEDS_REVIEW"),
        "approved": sum(1 for m in metrics if m.get("status") == "APPROVED"),
        "corrected": sum(1 for m in metrics if m.get("status") == "CORRECTED"),
        "rejected": sum(1 for m in metrics if m.get("status") == "REJECTED"),
    }
    return {**state, "counts": counts}


@router.get("/reviews")
def get_reviews(
    status: str = Query("pending", pattern="^(pending|completed|all)$"),
    run_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List metric records requiring or already completed by human review."""
    runs = [get_workflow_state(run_id)] if run_id else [get_workflow_state(r["run_id"]) for r in list_workflows()]
    results = []

    for r in runs:
        if not r:
            continue
        for m in r.get("metrics", []):
            m_status = m.get("status")
            if status == "pending" and m_status in ("NEEDS_REVIEW", "WAITING_FOR_REVIEW"):
                results.append(m)
            elif status == "completed" and m_status in ("APPROVED", "CORRECTED", "REJECTED"):
                results.append(m)
            elif status == "all" and m_status != "AUTO_APPROVED":
                results.append(m)

    return results


@router.post("/runs/{run_id}/review")
def submit_review(run_id: str, req: ReviewSubmissionRequest) -> Dict[str, Any]:
    """Submit a human review decision.

    If all pending reviews for this run are satisfied, resumes the workflow.
    Rejects duplicate submissions for already-reviewed metrics with HTTP 409.
    """
    state = get_workflow_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    metrics = {m["metric_id"]: m for m in state.get("metrics", [])}
    if req.metric_id not in metrics:
        raise HTTPException(status_code=400, detail=f"Metric {req.metric_id!r} does not belong to run {run_id!r}.")

    target_metric = metrics[req.metric_id]
    if target_metric.get("status") in ("APPROVED", "CORRECTED", "REJECTED"):
        raise HTTPException(
            status_code=409,
            detail=f"Metric {req.metric_id!r} was already decided as {target_metric.get('status')}.",
        )

    norm_decision = req.decision.strip().lower()
    if norm_decision not in ("approve", "correct", "reject"):
        raise HTTPException(status_code=400, detail="Decision must be 'approve', 'correct', or 'reject'.")

    if norm_decision == "correct" and req.corrected_value is None:
        raise HTTPException(status_code=400, detail="corrected_value is required when decision is 'correct'.")

    decisions = {
        req.metric_id: {
            "decision": norm_decision,
            "corrected_value": req.corrected_value,
            "reviewer": req.reviewer,
            "comment": req.comment,
        }
    }

    # Resume workflow with this decision
    updated_state = resume_workflow(run_id, decisions)
    remaining_pending = sum(1 for m in updated_state.get("metrics", []) if m.get("status") == "NEEDS_REVIEW")

    return {
        "status": "ok",
        "run_id": run_id,
        "metric_id": req.metric_id,
        "decision": norm_decision,
        "run_status": updated_state.get("status"),
        "remaining_reviews": remaining_pending,
    }


@router.get("/runs/{run_id}/accuracy-report")
def get_accuracy_report(run_id: str) -> Dict[str, Any]:
    """Return extraction accuracy & escalation precision report compared to SEC Company Facts."""
    state = get_workflow_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    report = state.get("accuracy_report")
    if not report:
        raise HTTPException(status_code=400, detail="Accuracy report not yet available for this run.")

    return report


@router.get("/audit-log")
def read_audit_log(run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return append-only audit decisions list."""
    return get_audit_records(run_id=run_id)


@router.get("/audit-log/verify")
def verify_audit_log() -> Dict[str, Any]:
    """Verify cryptographic SHA-256 hash-chain integrity of the audit log."""
    return verify_chain()


@router.get("/config/thresholds")
def read_thresholds() -> Dict[str, Any]:
    """Get active validation thresholds and confidence weights."""
    return get_config()


@router.put("/config/thresholds")
def write_thresholds(req: ConfigUpdateRequest) -> Dict[str, Any]:
    """Update active validation thresholds and persist to config/thresholds.yaml."""
    updates = req.model_dump(exclude_none=True)
    updated = update_config(updates)
    return {"status": "updated", "config": updated}
