"""End-to-end smoke test script for V.A.U.L.T Auditor Agent."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import HTTPException
from app.api.runs_routes import (
    ReviewSubmissionRequest,
    StartRunRequest,
    create_run,
    get_accuracy_report,
    get_reviews,
    get_run,
    read_audit_log,
    submit_review,
    verify_audit_log,
)


def run_smoke_test() -> int:
    print("=" * 70)
    print("🚀 V.A.U.L.T End-to-End Auditor Agent Smoke Test")
    print("=" * 70)

    # 1. Start run for Apple with deliberate scale_1000x injected error
    print("[1/6] Starting run with injected scale_1000x error...")
    run_res = create_run(
        StartRunRequest(cik="0000320193", fiscal_year=2023, inject_error="scale_1000x")
    )
    run_id = run_res["run_id"]
    print(f"      Run created: {run_id} | Status: {run_res['status']}")
    assert run_res["status"] == "WAITING_FOR_REVIEW", f"Expected WAITING_FOR_REVIEW, got {run_res['status']}"

    # 2. Check pending reviews
    print("[2/6] Inspecting pending review queue...")
    pending = get_reviews(status="pending", run_id=run_id)
    print(f"      Pending review count: {len(pending)}")
    assert len(pending) > 0, "Expected at least one pending metric"

    # 3. Submit correction for pending items
    print("[3/6] Submitting human auditor corrections...")
    initial_log_count = len(read_audit_log())
    for item in pending:
        m_id = item["metric_id"]
        sub_res = submit_review(
            run_id,
            ReviewSubmissionRequest(
                metric_id=m_id,
                decision="correct",
                corrected_value=float(item.get("value", 0.0) / 1000.0) if "revenue" in m_id else 62_146_000_000.0,
                reviewer="Chief Audit Partner",
                comment="Smoke test human correction against SEC Company Facts.",
            ),
        )
        print(f"      Submitted decision for {m_id}: {sub_res['status']}")

    # 4. Verify duplicate review submission returns HTTP 409
    print("[4/6] Verifying idempotency: duplicate submission must return 409...")
    target_metric = pending[0]["metric_id"]
    try:
        submit_review(
            run_id,
            ReviewSubmissionRequest(
                metric_id=target_metric,
                decision="approve",
                reviewer="Duplicate Submitter",
            ),
        )
        print("      FAILED: Duplicate submission was accepted!")
        return 1
    except HTTPException as e:
        assert e.status_code == 409, f"Expected 409, got {e.status_code}"
        print(f"      SUCCESS: Rejected duplicate with HTTP {e.status_code} ({e.detail})")

    # 5. Verify run completed & accuracy report generated
    print("[5/6] Verifying run completion & accuracy report...")
    final_state = get_run(run_id)
    print(f"      Final Run Status: {final_state['status']}")
    assert final_state["status"] == "COMPLETED", f"Expected COMPLETED, got {final_state['status']}"

    report = get_accuracy_report(run_id)
    print(f"      Accuracy: {report.get('overall_accuracy_pct')}% | Escalation Precision: {report.get('escalation_precision_pct')}%")

    # 6. Verify cryptographic audit log chain
    print("[6/6] Verifying cryptographic audit chain...")
    audit_res = verify_audit_log()
    current_log_count = len(read_audit_log())
    print(f"      New log entries added: {current_log_count - initial_log_count}")
    print(f"      Audit chain verification: {audit_res['status']} ({audit_res['total_records']} total records)")
    assert audit_res["verified"] is True, "Audit chain verification failed!"

    print("=" * 70)
    print("✅ SMOKE TEST RESULT: PASS")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(run_smoke_test())

