"""Automated tests for the Year-on-Year Financial Validation Agent."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.yoy_routes import get_audit_store
from app.main import app
from app.models.yoy_models import (
    FinalStatus,
    HumanAction,
    HumanDecision,
    HumanReviewRequest,
    ValidationResult,
    YearMetricInput,
    YoYValidationRequest,
)
from app.services.audit_logger import AuditStore
from app.services.yoy_validator import (
    apply_human_review,
    calculate_yoy_percent,
    validate_yoy,
)


def _make_request(
    prev_val: object,
    curr_val: object,
    prev_year: int | None = 2024,
    curr_year: int | None = 2025,
    prev_unit: str | None = "USD millions",
    curr_unit: str | None = "USD millions",
    threshold: object = 30,
    metric: str = "Revenue",
    company: str = "Apple Inc.",
    confidence: float | None = 0.90,
    prev_metric: str | None = None,
    curr_metric: str | None = None,
) -> YoYValidationRequest:
    return YoYValidationRequest(
        company=company,
        metric=metric,
        previous_year=YearMetricInput(
            year=prev_year,
            value=prev_val,
            unit=prev_unit,
            metric=prev_metric,
        ),
        current_year=YearMetricInput(
            year=curr_year,
            value=curr_val,
            unit=curr_unit,
            metric=curr_metric,
        ),
        threshold_percent=threshold,
        automated_confidence=confidence,
    )


# --- Required Core Scenarios (Section 15) ---


def test_plus_10_percent_pass() -> None:
    """1. +10% -> PASS"""
    resp = validate_yoy(_make_request(prev_val=100, curr_val=110))
    assert resp.validation_result == ValidationResult.PASS
    assert resp.status == ValidationResult.PASS
    assert resp.yoy_percent == 10.0
    assert resp.human_review_required is False
    assert resp.final_status == FinalStatus.PASSED


def test_plus_30_percent_exact_threshold_pass() -> None:
    """2. +30% -> PASS (threshold comparison is strict abs(YoY) > threshold)"""
    resp = validate_yoy(_make_request(prev_val=100, curr_val=130))
    assert resp.validation_result == ValidationResult.PASS
    assert resp.yoy_percent == 30.0
    assert resp.human_review_required is False


def test_plus_30_point_1_percent_unusual() -> None:
    """3. +30.1% -> UNUSUAL"""
    resp = validate_yoy(_make_request(prev_val=100, curr_val="130.1"))
    assert resp.validation_result == ValidationResult.UNUSUAL
    assert resp.yoy_percent == 30.1
    assert resp.human_review_required is True
    assert resp.final_status == FinalStatus.PENDING_HUMAN_REVIEW
    # Must NOT claim the value is wrong/incorrect
    assert "wrong" not in resp.reason.lower()
    assert "incorrect" not in resp.reason.lower()


def test_minus_40_percent_unusual_preserves_sign() -> None:
    """4. -40% -> UNUSUAL and preserves negative sign."""
    resp = validate_yoy(_make_request(prev_val=100, curr_val=60))
    assert resp.validation_result == ValidationResult.UNUSUAL
    assert resp.yoy_percent == -40.0
    assert resp.human_review_required is True
    assert "-40" in resp.reason


def test_previous_value_zero_cannot_validate() -> None:
    """5. Previous value = 0 -> CANNOT_VALIDATE (never infinity)."""
    resp = validate_yoy(_make_request(prev_val=0, curr_val=100))
    assert resp.validation_result == ValidationResult.CANNOT_VALIDATE
    assert resp.status == ValidationResult.CANNOT_VALIDATE
    assert resp.yoy_percent is None
    assert resp.human_review_required is False
    assert "zero" in resp.reason.lower()


def test_missing_previous_year_cannot_validate() -> None:
    """6. Missing previous year -> CANNOT_VALIDATE"""
    req = YoYValidationRequest(
        company="Apple Inc.",
        metric="Revenue",
        current_year=YearMetricInput(year=2025, value=145, unit="USD millions"),
        previous_year=None,
        threshold_percent=30,
    )
    resp = validate_yoy(req)
    assert resp.validation_result == ValidationResult.CANNOT_VALIDATE
    assert resp.yoy_percent is None

    # Also test when previous_year object exists but year number is missing
    resp_no_yr = validate_yoy(_make_request(prev_val=100, curr_val=145, prev_year=None))
    assert resp_no_yr.validation_result == ValidationResult.CANNOT_VALIDATE


def test_different_units_normalized_correctly() -> None:
    """7. Different units ($500 million vs $0.5 billion) -> normalized correctly."""
    resp = validate_yoy(
        _make_request(
            prev_val="0.5",
            prev_unit="$ billion",
            curr_val="500",
            curr_unit="$ million",
        )
    )
    assert resp.validation_result == ValidationResult.PASS
    assert resp.yoy_percent == 0.0
    assert resp.unit == "USD millions"
    assert resp.previous_value == 500.0
    assert resp.current_value == 500.0
    assert resp.unit_normalization is not None
    assert resp.unit_normalization.previous_year.original_unit == "$ billion"
    assert resp.unit_normalization.previous_year.normalized_unit == "USD millions"
    assert resp.unit_normalization.previous_year.conversion_applied is True
    assert resp.unit_normalization.current_year.conversion_applied is False


def test_current_year_earlier_than_previous_year_cannot_validate() -> None:
    """8. Current year earlier than (or equal to) previous year -> CANNOT_VALIDATE."""
    resp = validate_yoy(
        _make_request(prev_val=100, curr_val=110, prev_year=2025, curr_year=2024)
    )
    assert resp.validation_result == ValidationResult.CANNOT_VALIDATE
    assert resp.yoy_percent is None
    assert "later than" in resp.reason.lower()

    resp_equal = validate_yoy(
        _make_request(prev_val=100, curr_val=110, prev_year=2025, curr_year=2025)
    )
    assert resp_equal.validation_result == ValidationResult.CANNOT_VALIDATE


def test_negative_metric_value_preserved_and_explained() -> None:
    """9. Negative financial metric values are not rejected and preserve exact math."""
    # Previous = -100, Current = -140 => ((-140 - (-100)) / -100) * 100 = +40%
    resp = validate_yoy(
        _make_request(
            prev_val=-100,
            curr_val=-140,
            metric="Net Income",
        )
    )
    assert resp.validation_result == ValidationResult.UNUSUAL
    assert resp.yoy_percent == 40.0
    assert "negative" in resp.reason.lower()


def test_exact_decimal_calculations_avoid_float_drift() -> None:
    """10. Exact decimal calculations avoid IEEE-754 float drift (e.g., 1.0 -> 1.3 is +30%)."""
    # In binary float, ((1.3 - 1.0) / 1.0) * 100 == 30.000000000000004 > 30!
    raw_yoy = calculate_yoy_percent(Decimal("1.3"), Decimal("1.0"))
    assert raw_yoy == Decimal("30.0")

    resp = validate_yoy(_make_request(prev_val=1.0, curr_val=1.3, threshold=30))
    assert resp.yoy_percent == 30.0
    assert resp.validation_result == ValidationResult.PASS


# --- Additional Guardrails & Architecture Tests ---


def test_incompatible_units_and_metric_mismatch_cannot_validate() -> None:
    """Incompatible currency/dimension units or mismatched metrics must not validate."""
    diff_currency = validate_yoy(
        _make_request(
            prev_val=100,
            prev_unit="EUR millions",
            curr_val=120,
            curr_unit="USD millions",
        )
    )
    assert diff_currency.validation_result == ValidationResult.CANNOT_VALIDATE

    per_share_vs_total = validate_yoy(
        _make_request(
            prev_val=5,
            prev_unit="USD per share",
            curr_val=120,
            curr_unit="USD millions",
        )
    )
    assert per_share_vs_total.validation_result == ValidationResult.CANNOT_VALIDATE

    metric_mismatch = validate_yoy(
        _make_request(
            prev_val=100,
            curr_val=110,
            metric="Revenue",
            prev_metric="Net Income",
            curr_metric="Revenue",
        )
    )
    assert metric_mismatch.validation_result == ValidationResult.CANNOT_VALIDATE


def test_small_denominator_flags_warning_with_context() -> None:
    """Small denominator (e.g. 0.1 -> 1.0) preserves math and surfaces underlying context."""
    resp = validate_yoy(_make_request(prev_val=0.1, curr_val=1.0))
    assert resp.validation_result == ValidationResult.UNUSUAL
    assert resp.yoy_percent == 900.0
    assert resp.small_denominator_warning is True
    assert "0.1" in resp.reason
    assert "1" in resp.reason


def test_configurable_threshold() -> None:
    """Threshold is configurable and not hardcoded to 30%."""
    resp = validate_yoy(_make_request(prev_val=100, curr_val=115, threshold=10))
    assert resp.validation_result == ValidationResult.UNUSUAL
    assert resp.threshold_percent == 10.0

    resp_wide = validate_yoy(_make_request(prev_val=100, curr_val=145, threshold=50))
    assert resp_wide.validation_result == ValidationResult.PASS
    assert resp_wide.threshold_percent == 50.0


def test_human_review_preserves_confidence_and_original_value() -> None:
    """Human approval/correction never overwrites automated_confidence or original_value."""
    initial = validate_yoy(
        _make_request(prev_val=100, curr_val=145, threshold=30, confidence=0.90)
    )
    assert initial.automated_confidence == 0.90
    assert initial.validation_result == ValidationResult.UNUSUAL

    approved = apply_human_review(
        initial,
        HumanReviewRequest(
            validation_id=initial.validation_id,
            action=HumanAction.APPROVE,
            reviewer="auditor_1",
        ),
    )
    assert approved.human_decision == HumanDecision.APPROVED
    assert approved.human_verified is True
    assert approved.final_status == FinalStatus.HUMAN_VERIFIED
    assert approved.automated_confidence == 0.90
    assert approved.original_value == 145.0
    assert approved.corrected_value is None

    corrected = apply_human_review(
        initial,
        HumanReviewRequest(
            validation_id=initial.validation_id,
            action=HumanAction.CORRECT,
            corrected_value="142.5",
            reviewer="auditor_1",
        ),
    )
    assert corrected.human_decision == HumanDecision.CORRECTED
    assert corrected.human_verified is True
    assert corrected.final_status == FinalStatus.HUMAN_CORRECTED
    assert corrected.original_value == 145.0
    assert corrected.corrected_value == 142.5
    assert corrected.automated_confidence == 0.90


def test_fastapi_endpoints_and_audit_trail(tmp_path: Path) -> None:
    """End-to-end API flow: validate -> review -> audit trail JSONL persistence."""
    log_file = tmp_path / "audit.jsonl"
    test_store = AuditStore(log_file=log_file)
    app.dependency_overrides[get_audit_store] = lambda: test_store
    try:
        client = TestClient(app)

        validate_res = client.post(
            "/api/v1/yoy/validate",
            json={
                "company": "Apple Inc.",
                "metric": "Revenue",
                "current_year": {"year": 2025, "value": 145, "unit": "USD millions"},
                "previous_year": {"year": 2024, "value": 100, "unit": "USD millions"},
                "threshold_percent": 30,
                "automated_confidence": 0.90,
            },
        )
        assert validate_res.status_code == 200
        body = validate_res.json()
        assert body["validation_result"] == "UNUSUAL"
        assert body["yoy_percent"] == 45.0
        assert body["human_review_required"] is True
        assert body["automated_confidence"] == 0.90

        review_res = client.post(
            "/api/v1/yoy/review",
            json={
                "validation_id": body["validation_id"],
                "action": "APPROVE",
                "reviewer": "jane.doe",
                "notes": "Verified against SEC 10-K Item 7.",
            },
        )
        assert review_res.status_code == 200
        reviewed = review_res.json()
        assert reviewed["human_decision"] == "APPROVED"
        assert reviewed["human_verified"] is True
        assert reviewed["final_status"] == "HUMAN_VERIFIED"
        assert reviewed["automated_confidence"] == 0.90
        assert reviewed["original_value"] == 145.0
        assert reviewed["corrected_value"] is None

        audit_res = client.get(
            f"/api/v1/yoy/audit-trail?validation_id={body['validation_id']}"
        )
        assert audit_res.status_code == 200
        entries = audit_res.json()
        assert len(entries) == 2
        assert entries[0]["human_decision"] is None
        assert entries[1]["human_decision"] == "APPROVED"
        assert entries[1]["reviewer"] == "jane.doe"
        assert log_file.exists()
        assert len(log_file.read_text(encoding="utf-8").splitlines()) == 2

        # Verify demo UI and health endpoint
        health_res = client.get("/health")
        assert health_res.status_code == 200
        ui_res = client.get("/")
        assert ui_res.status_code == 200
        assert "Year-on-Year Financial Validation Agent" in ui_res.text

        # Verify REJECT action on another validation
        val2 = client.post(
            "/api/v1/yoy/validate",
            json={
                "company": "Apple Inc.",
                "metric": "Revenue",
                "current_year": {"year": 2025, "value": 416161, "unit": "USD millions"},
                "previous_year": {"year": 2024, "value": 391035, "unit": "USD millions"},
                "threshold_percent": 30,
            },
        )
        assert val2.status_code == 200
        val2_body = val2.json()
        assert val2_body["validation_result"] == "PASS"
        assert val2_body["yoy_percent"] == pytest.approx(6.425512, rel=1e-5)

        rej = client.post(
            "/api/v1/yoy/review",
            json={
                "validation_id": val2_body["validation_id"],
                "action": "REJECT",
                "reviewer": "auditor_2",
            },
        )
        assert rej.status_code == 200
        assert rej.json()["human_decision"] == "REJECTED"
        assert rej.json()["human_verified"] is False
        assert rej.json()["final_status"] == "HUMAN_REJECTED"
    finally:
        app.dependency_overrides.clear()


def test_cli_single_metric_and_html_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLI validates single metrics via flags and generates reports from SEC HTML files."""
    from app.cli import main as cli_main

    monkeypatch.setattr("app.cli.DEFAULT_AUDIT_LOG", tmp_path / "cli_audit.jsonl")

    exit_code = cli_main(
        [
            "--company",
            "Apple Inc.",
            "--metric",
            "Revenue",
            "--prev-year",
            "2024",
            "--prev-value",
            "100",
            "--prev-unit",
            "USD millions",
            "--curr-year",
            "2025",
            "--curr-value",
            "145",
            "--curr-unit",
            "USD millions",
            "--threshold",
            "30",
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "UNUSUAL" in out
    assert "+45.0%" in out

    sample_html = """
    <html><body>
    <div>Example Corp. Consolidated Statements of Income</div>
    <table>
      <tr><td>(Amounts in millions, except per share data)</td><td>2026</td><td>2025</td></tr>
      <tr><td>Net sales</td><td>$ 145</td><td>$ 100</td></tr>
      <tr><td>Diluted net income per common share</td><td>$ 2.50</td><td>$ 2.00</td></tr>
    </table>
    </body></html>
    """
    html_file = tmp_path / "sample_10k.htm"
    html_file.write_text(sample_html, encoding="utf-8")
    md_out = tmp_path / "report.md"

    exit_html = cli_main(
        [
            "--html-file",
            str(html_file),
            "--report-out",
            str(md_out),
        ]
    )
    assert exit_html == 0
    assert md_out.exists()
    report_txt = md_out.read_text(encoding="utf-8")
    assert "Net sales" in report_txt
    assert "UNUSUAL" in report_txt
    assert "USD per share" in report_txt
