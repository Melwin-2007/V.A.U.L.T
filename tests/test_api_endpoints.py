"""In-process API endpoint unit test suite testing routes and schemas directly."""

from __future__ import annotations

import unittest
from fastapi import HTTPException

from app.api.runs_routes import (
    ConfigUpdateRequest,
    ReviewSubmissionRequest,
    StartRunRequest,
    create_run,
    get_accuracy_report,
    get_reviews,
    get_run,
    list_runs,
    read_audit_log,
    read_thresholds,
    submit_review,
    verify_audit_log,
    write_thresholds,
)


class TestRunsApiDirect(unittest.TestCase):
    def test_end_to_end_run_pause_and_review(self) -> None:
        # 1. Start run for Apple (stub contains equity anomaly)
        req = StartRunRequest(cik="0000320193", fiscal_year=2023)
        res = create_run(req)
        run_id = res["run_id"]
        self.assertEqual(res["status"], "WAITING_FOR_REVIEW")
        self.assertGreater(res["counts"]["needs_review"], 0)

        # 2. Get run details
        run_detail = get_run(run_id)
        self.assertEqual(run_detail["run_id"], run_id)
        self.assertEqual(run_detail["status"], "WAITING_FOR_REVIEW")

        # 3. List runs
        all_runs = list_runs()
        self.assertIn(run_id, [r["run_id"] for r in all_runs])

        # 4. Check pending reviews
        pending_list = get_reviews(status="pending", run_id=run_id)
        self.assertGreater(len(pending_list), 0)

        # 5. Submit review for all pending metrics
        for p in pending_list:
            m_id = p["metric_id"]
            sub_req = ReviewSubmissionRequest(
                metric_id=m_id,
                decision="correct",
                corrected_value=62_146_000_000.0,
                reviewer="Sarah Connor (Audit Lead)",
                comment="Aligned with ground truth Company Facts.",
            )
            sub_res = submit_review(run_id, sub_req)
            self.assertEqual(sub_res["status"], "ok")

            # Duplicate review submission must raise HTTPException 409 Conflict
            with self.assertRaises(HTTPException) as ctx:
                submit_review(run_id, sub_req)
            self.assertEqual(ctx.exception.status_code, 409)

        # 6. Verify run is now COMPLETED
        final_detail = get_run(run_id)
        self.assertEqual(final_detail["status"], "COMPLETED")

        # 7. Verify accuracy report
        acc_report = get_accuracy_report(run_id)
        self.assertIn("overall_accuracy_pct", acc_report)
        self.assertGreaterEqual(acc_report["overall_accuracy_pct"], 0.0)

        # 8. Verify audit log integrity
        audit_verify = verify_audit_log()
        self.assertTrue(audit_verify["verified"])
        self.assertEqual(audit_verify["status"], "VALID")

        # 9. Read audit log entries for run
        run_audit = read_audit_log(run_id=run_id)
        self.assertGreater(len(run_audit), 0)

    def test_config_endpoints(self) -> None:
        cfg = read_thresholds()
        self.assertIn("confidence_threshold", cfg)

        updated = write_thresholds(ConfigUpdateRequest(confidence_threshold=0.89))
        self.assertEqual(updated["config"]["confidence_threshold"], 0.89)

        # Restore default
        write_thresholds(ConfigUpdateRequest(confidence_threshold=0.85))


if __name__ == "__main__":
    unittest.main()
