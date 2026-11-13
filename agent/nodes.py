"""Workflow step node functions for the V.A.U.L.T Agent."""

from __future__ import annotations

import copy
import os
from typing import Any, Dict, List, Optional

from agent.audit import log_decision
from agent.config import get_config
from agent.state import AgentWorkflowState


def _get_pipeline_module() -> Any:
    use_stubs = os.getenv("USE_STUBS", "false").strip().lower() in ("true", "1", "yes")
    if use_stubs:
        import pipeline.stubs as pipeline_mod
        return pipeline_mod
    try:
        import pipeline as pipeline_mod
        return pipeline_mod
    except ImportError:
        import pipeline.stubs as pipeline_mod
        return pipeline_mod


def fetch_filing_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 1: Fetch 10-K filing metadata and contents."""
    completed = list(state.get("completed_steps") or [])
    if "fetch_filing" in completed and state.get("filing"):
        return state

    cik = state.get("cik", "0000320193")
    year = state.get("fiscal_year", 2023)
    pipe = _get_pipeline_module()
    filing = pipe.fetch_filing(cik=cik, fiscal_year=year)

    state["filing"] = filing
    state["company"] = filing.get("company", state.get("company", "Unknown"))
    if "fetch_filing" not in completed:
        completed.append("fetch_filing")
    state["completed_steps"] = completed
    return state


def extract_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 2: Extract standard 10 metric records from filing."""
    completed = list(state.get("completed_steps") or [])
    if "extract" in completed and state.get("metrics"):
        return state

    filing = state.get("filing") or {}
    pipe = _get_pipeline_module()
    metrics = pipe.extract_metrics(filing)

    # Optional error injection for live demonstration
    inject_err = state.get("inject_error")
    if inject_err:
        metrics = _apply_error_injection(metrics, inject_err)

    state["metrics"] = metrics
    if "extract" not in completed:
        completed.append("extract")
    state["completed_steps"] = completed
    return state


def score_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 3: Compute 4-factor confidence score for all extracted metrics."""
    completed = list(state.get("completed_steps") or [])
    if "score" in completed:
        return state

    metrics = state.get("metrics") or []
    cfg = get_config()
    pipe = _get_pipeline_module()
    scored = pipe.score_metrics(metrics, config=cfg)

    state["metrics"] = scored
    if "score" not in completed:
        completed.append("score")
    state["completed_steps"] = completed
    return state


def validate_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 4: Execute accounting equation, segments, and YoY validations."""
    completed = list(state.get("completed_steps") or [])
    if "validate" in completed and state.get("checks"):
        return state

    metrics = state.get("metrics") or []
    cfg = get_config()
    pipe = _get_pipeline_module()
    validated_metrics, checks = pipe.run_validations(metrics, config=cfg)

    state["metrics"] = validated_metrics
    state["checks"] = checks
    if "validate" not in completed:
        completed.append("validate")
    state["completed_steps"] = completed
    return state


def decide_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 5: Determine AUTO_APPROVED vs. NEEDS_REVIEW per metric."""
    completed = list(state.get("completed_steps") or [])
    if "decide" in completed:
        return state

    cfg = get_config()
    threshold = float(cfg.get("confidence_threshold", 0.85))
    auto_approve_enabled = bool(cfg.get("auto_approve_enabled", True))
    checks = state.get("checks") or []
    hard_failed_checks = [c for c in checks if (not c.get("passed")) and c.get("severity") == "hard"]

    metrics = list(state.get("metrics") or [])
    updated = []
    for m in metrics:
        rec = dict(m)
        m_name = rec.get("metric_name")
        conf = float(rec.get("confidence", 0.0))

        # Check if touched by any hard failure
        failing_hard = [c for c in hard_failed_checks if m_name in c.get("affected_metrics", [])]

        if not auto_approve_enabled:
            rec["status"] = "NEEDS_REVIEW"
            rec["escalation_reason"] = "Auto-approval globally disabled by policy."
            rec["final_value"] = None
        elif failing_hard:
            rec["status"] = "NEEDS_REVIEW"
            rec["escalation_reason"] = (
                f"Trigger B: Hard Accounting Rule Failure: {failing_hard[0].get('message')}"
            )
            rec["final_value"] = None
        elif conf < threshold:
            rec["status"] = "NEEDS_REVIEW"
            rec["escalation_reason"] = f"Trigger A: Low Confidence ({conf:.2f} < {threshold:.2f})."
            rec["final_value"] = None
        else:
            rec["status"] = "AUTO_APPROVED"
            rec["escalation_reason"] = None
            rec["final_value"] = rec.get("value")

        updated.append(rec)

    state["metrics"] = updated
    if "decide" not in completed:
        completed.append("decide")
    state["completed_steps"] = completed
    return state


def human_review_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 6: Pause execution if any metric requires human review."""
    metrics = state.get("metrics") or []
    pending_review = [m for m in metrics if m.get("status") == "NEEDS_REVIEW"]

    if pending_review:
        state["status"] = "WAITING_FOR_REVIEW"
    else:
        state["status"] = "RUNNING"

    completed = list(state.get("completed_steps") or [])
    if "human_review" not in completed:
        completed.append("human_review")
    state["completed_steps"] = completed
    return state


def apply_decisions_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 7: Apply human review decisions and log audit trail."""
    decisions = state.get("decisions") or {}
    metrics = list(state.get("metrics") or [])
    run_id = state.get("run_id", "unknown_run")

    updated = []
    for m in metrics:
        rec = dict(m)
        m_id = rec.get("metric_id")

        if m_id in decisions and rec.get("status") in ("NEEDS_REVIEW", "WAITING_FOR_REVIEW"):
            dec_entry = decisions[m_id]
            action = dec_entry.get("decision", "approve").lower()
            reviewer = dec_entry.get("reviewer", "Human Reviewer")
            comment = dec_entry.get("comment", "")
            orig_val = rec.get("value")

            if action == "approve":
                rec["status"] = "APPROVED"
                rec["final_value"] = orig_val
                log_decision(run_id, m_id, "approve", orig_val, reviewer=reviewer, comment=comment)
            elif action == "correct":
                corrected_val = dec_entry.get("corrected_value")
                rec["status"] = "CORRECTED"
                rec["final_value"] = corrected_val
                log_decision(run_id, m_id, "correct", orig_val, corrected_value=corrected_val, reviewer=reviewer, comment=comment)
            elif action == "reject":
                rec["status"] = "REJECTED"
                rec["final_value"] = None
                log_decision(run_id, m_id, "reject", orig_val, reviewer=reviewer, comment=comment)

        updated.append(rec)

    state["metrics"] = updated
    completed = list(state.get("completed_steps") or [])
    if "apply_decisions" not in completed:
        completed.append("apply_decisions")
    state["completed_steps"] = completed
    return state


def ground_truth_verify_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 8: Grade extractions against official SEC Company Facts."""
    completed = list(state.get("completed_steps") or [])
    if "ground_truth_verify" in completed and state.get("accuracy_report"):
        return state

    cik = state.get("cik", "0000320193")
    year = state.get("fiscal_year", 2023)
    metrics = state.get("metrics") or []

    pipe = _get_pipeline_module()
    gt = pipe.fetch_ground_truth(cik=cik, fiscal_year=year)
    report = pipe.build_accuracy_report(metrics, gt)

    state["accuracy_report"] = report
    if "ground_truth_verify" not in completed:
        completed.append("ground_truth_verify")
    state["completed_steps"] = completed
    return state


def final_report_node(state: AgentWorkflowState) -> AgentWorkflowState:
    """Step 9: Finalize run status and calculate summary metrics."""
    metrics = state.get("metrics") or []
    needs_review = [m for m in metrics if m.get("status") == "NEEDS_REVIEW"]

    if needs_review:
        state["status"] = "WAITING_FOR_REVIEW"
    else:
        state["status"] = "COMPLETED"

    completed = list(state.get("completed_steps") or [])
    if "final_report" not in completed:
        completed.append("final_report")
    state["completed_steps"] = completed
    return state


def _apply_error_injection(metrics: List[Dict[str, Any]], inject_type: str) -> List[Dict[str, Any]]:
    """Inject deliberate errors for live judge demonstrations."""
    from pipeline.error_injection import apply_error_injection
    return apply_error_injection(metrics, inject_type)
