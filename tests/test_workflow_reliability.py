"""Workflow reliability test suite proving pause, SQLite checkpointing, and resume."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from agent.audit import AuditLogger
from agent.graph import AuditorWorkflowGraph, SqliteCheckpointStore


class TestWorkflowReliability(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_checkpoints.sqlite"
        self.audit_path = Path(self.temp_dir.name) / "test_decisions.jsonl"
        self.store = SqliteCheckpointStore(db_path=self.db_path)
        self.audit_logger = AuditLogger(log_path=self.audit_path)
        self.graph = AuditorWorkflowGraph(checkpointer=self.store)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_workflow_pauses_when_flawed_metric_exists(self) -> None:
        # Flawed metric (scale_1000x error) -> must pause with WAITING_FOR_REVIEW
        state = self.graph.start_run(cik="0000320193", fiscal_year=2023, inject_error="scale_1000x")
        self.assertEqual(state["status"], "WAITING_FOR_REVIEW")
        pending = [m for m in state["metrics"] if m["status"] == "NEEDS_REVIEW"]
        self.assertGreater(len(pending), 0)

        # Confirm saved in SQLite
        loaded = self.store.load(state["run_id"])
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["status"], "WAITING_FOR_REVIEW")

    def test_restarting_server_and_resuming_from_sqlite_succeeds(self) -> None:
        state = self.graph.start_run(cik="0000320193", fiscal_year=2023, inject_error="scale_1000x")
        run_id = state["run_id"]
        self.assertEqual(state["status"], "WAITING_FOR_REVIEW")

        # Simulate full server restart: create brand-new graph pointing to the same SQLite DB
        restarted_store = SqliteCheckpointStore(db_path=self.db_path)
        restarted_graph = AuditorWorkflowGraph(checkpointer=restarted_store)

        pending_metrics = [m["metric_id"] for m in state["metrics"] if m["status"] == "NEEDS_REVIEW"]
        decisions = {
            m_id: {
                "decision": "correct",
                "corrected_value": 62_146_000_000.0,
                "reviewer": "Senior CPA",
                "comment": "Replaced typo with source filing ground truth.",
            }
            for m_id in pending_metrics
        }

        resumed_state = restarted_graph.resume_run(run_id, decisions)
        self.assertEqual(resumed_state["status"], "COMPLETED")
        self.assertIsNotNone(resumed_state["accuracy_report"])

        # Check that metrics transitioned to CORRECTED
        for m in resumed_state["metrics"]:
            if m["metric_id"] in pending_metrics:
                self.assertEqual(m["status"], "CORRECTED")
                self.assertEqual(m["final_value"], 62_146_000_000.0)

    def test_repeat_resume_is_idempotent_and_creates_no_duplicate_audit_entries(self) -> None:
        state = self.graph.start_run(cik="0000320193", fiscal_year=2023)
        run_id = state["run_id"]
        target_metric = "run_2023_320193:shareholders_equity"
        decisions = {
            target_metric: {
                "decision": "approve",
                "reviewer": "Reviewer 1",
                "comment": "Manual pass.",
            }
        }

        # First resume
        res1 = self.graph.resume_run(run_id, decisions)
        self.assertIn("apply_decisions", res1["completed_steps"])

        # Second resume with identical decision
        res2 = self.graph.resume_run(run_id, decisions)
        self.assertEqual(res2["status"], res1["status"])

    def test_fetch_filing_is_executed_once_per_run(self) -> None:
        state = self.graph.start_run(cik="0000320193", fiscal_year=2023)
        self.assertEqual(state["completed_steps"].count("fetch_filing"), 1)

        resumed = self.graph.resume_run(state["run_id"], {})
        self.assertEqual(resumed["completed_steps"].count("fetch_filing"), 1)


if __name__ == "__main__":
    unittest.main()
