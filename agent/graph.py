"""Stateful workflow engine with SQLite checkpointing and pause/resume execution."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from agent.nodes import (
    apply_decisions_node,
    decide_node,
    extract_node,
    fetch_filing_node,
    final_report_node,
    ground_truth_verify_node,
    human_review_node,
    score_node,
    validate_node,
)
from agent.state import AgentWorkflowState

DEFAULT_CHECKPOINT_DB = Path("data/checkpoints.sqlite")


class SqliteCheckpointStore:
    """Persistent SQLite store for workflow execution checkpoints."""

    def __init__(self, db_path: Path = DEFAULT_CHECKPOINT_DB) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS checkpoints (
                        run_id TEXT PRIMARY KEY,
                        cik TEXT,
                        company TEXT,
                        fiscal_year INTEGER,
                        status TEXT,
                        state_json TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    )
                    """
                )
        finally:
            conn.close()

    def save(self, state: AgentWorkflowState) -> None:
        run_id = state["run_id"]
        cik = state.get("cik", "")
        company = state.get("company", "")
        year = state.get("fiscal_year", 2023)
        status = state.get("status", "RUNNING")
        now = datetime.now(timezone.utc).isoformat()
        state["updated_at"] = now
        if not state.get("created_at"):
            state["created_at"] = now

        state_json = json.dumps(state)

        conn = self._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO checkpoints (run_id, cik, company, fiscal_year, status, state_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id) DO UPDATE SET
                        status=excluded.status,
                        company=excluded.company,
                        state_json=excluded.state_json,
                        updated_at=excluded.updated_at
                    """,
                    (run_id, cik, company, year, status, state_json, state["created_at"], now),
                )
        finally:
            conn.close()

    def load(self, run_id: str) -> Optional[AgentWorkflowState]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                "SELECT state_json FROM checkpoints WHERE run_id = ?",
                (run_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return json.loads(row["state_json"])
        finally:
            conn.close()

    def list_all(self) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        try:
            cursor = conn.execute(
                """
                SELECT run_id, cik, company, fiscal_year, status, created_at, updated_at, state_json
                FROM checkpoints
                ORDER BY updated_at DESC
                """
            )
            results = []
            for row in cursor.fetchall():
                state_data = json.loads(row["state_json"])
                metrics = state_data.get("metrics", [])
                counts = {
                    "total": len(metrics),
                    "auto_approved": sum(1 for m in metrics if m.get("status") == "AUTO_APPROVED"),
                    "needs_review": sum(1 for m in metrics if m.get("status") == "NEEDS_REVIEW"),
                    "approved": sum(1 for m in metrics if m.get("status") == "APPROVED"),
                    "corrected": sum(1 for m in metrics if m.get("status") == "CORRECTED"),
                    "rejected": sum(1 for m in metrics if m.get("status") == "REJECTED"),
                }
                results.append(
                    {
                        "run_id": row["run_id"],
                        "cik": row["cik"],
                        "company": row["company"],
                        "fiscal_year": row["fiscal_year"],
                        "status": row["status"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "counts": counts,
                    }
                )
            return results
        finally:
            conn.close()


class AuditorWorkflowGraph:
    """Stateful workflow executor executing pipeline steps with pause & resume."""

    def __init__(self, checkpointer: Optional[SqliteCheckpointStore] = None) -> None:
        self.checkpointer = checkpointer or SqliteCheckpointStore()

    def start_run(
        self,
        cik: str,
        fiscal_year: int = 2023,
        inject_error: Optional[str] = None,
    ) -> AgentWorkflowState:
        """Start a new workflow run.

        Executes up to human_review. If any metric needs review, pauses and returns
        with status="WAITING_FOR_REVIEW". Otherwise, continues to completion.
        """
        norm_cik = str(cik).strip().zfill(10)
        run_id = f"run_{fiscal_year}_{norm_cik[-6:]}_{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc).isoformat()

        state: AgentWorkflowState = {
            "run_id": run_id,
            "cik": norm_cik,
            "fiscal_year": fiscal_year,
            "company": "Loading...",
            "status": "RUNNING",
            "inject_error": inject_error,
            "filing": {},
            "metrics": [],
            "checks": [],
            "decisions": {},
            "accuracy_report": None,
            "completed_steps": [],
            "created_at": now,
            "updated_at": now,
        }

        # Step 1: Fetch
        state = fetch_filing_node(state)
        # Step 2: Extract
        state = extract_node(state)
        # Step 3: Score
        state = score_node(state)
        # Step 4: Validate
        state = validate_node(state)
        # Step 5: Decide
        state = decide_node(state)
        # Step 6: Review check
        state = human_review_node(state)

        if state["status"] == "WAITING_FOR_REVIEW":
            # Interrupt and checkpoint state
            self.checkpointer.save(state)
            return state

        # If clean, complete remaining steps automatically
        state = apply_decisions_node(state)
        state = ground_truth_verify_node(state)
        state = final_report_node(state)
        self.checkpointer.save(state)
        return state

    def resume_run(
        self,
        run_id: str,
        decisions: Dict[str, Dict[str, Any]],
    ) -> AgentWorkflowState:
        """Resume a paused workflow run after human decisions are provided.

        Idempotent: merges new decisions into existing decisions, executes
        post-review nodes, and updates the checkpoint to COMPLETED.
        """
        state = self.checkpointer.load(run_id)
        if not state:
            raise KeyError(f"Run ID {run_id!r} not found in checkpoints.")

        existing_decisions = dict(state.get("decisions") or {})
        existing_decisions.update(decisions)
        state["decisions"] = existing_decisions
        state["status"] = "RUNNING"

        # Resume downstream steps
        state = apply_decisions_node(state)
        state = ground_truth_verify_node(state)
        state = final_report_node(state)

        self.checkpointer.save(state)
        return state

    def get_state(self, run_id: str) -> Optional[AgentWorkflowState]:
        return self.checkpointer.load(run_id)

    def list_runs(self) -> List[Dict[str, Any]]:
        return self.checkpointer.list_all()


_GLOBAL_WORKFLOW_GRAPH = AuditorWorkflowGraph()


def start_workflow(
    cik: str,
    fiscal_year: int = 2023,
    inject_error: Optional[str] = None,
) -> AgentWorkflowState:
    return _GLOBAL_WORKFLOW_GRAPH.start_run(cik, fiscal_year, inject_error=inject_error)


def resume_workflow(
    run_id: str,
    decisions: Dict[str, Dict[str, Any]],
) -> AgentWorkflowState:
    return _GLOBAL_WORKFLOW_GRAPH.resume_run(run_id, decisions)


def get_workflow_state(run_id: str) -> Optional[AgentWorkflowState]:
    return _GLOBAL_WORKFLOW_GRAPH.get_state(run_id)


def list_workflows() -> List[Dict[str, Any]]:
    return _GLOBAL_WORKFLOW_GRAPH.list_runs()
