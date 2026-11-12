"""Pytest suite for the Year-on-Year (YoY) Financial Validation module."""

from __future__ import annotations

import json

from validation.integration import (
    apply_yoy_to_confidence,
    to_escalation,
    yoy_validation_node,
)
from validation.yoy import analyze_company, check_yoy, load_thresholds
from validation.yoy_explain import explain
from validation.yoy_multi import check_yoy_multi


def test_normal_change_ok_and_warn() -> None:
    thresholds = load_thresholds()
    ok_res = check_yoy("revenue", 110, 100, thresholds)
    assert ok_res["check"] == "yoy_change"
    assert ok_res["passed"] is True
    assert ok_res["severity"] == "ok"
    assert ok_res["pct_change"] == 10.0
    assert ok_res["abs_change"] == 10.0
    assert ok_res["threshold_used"] == 50.0

    # 40% is >= 75% of 50% (37.5%) and <= 50% -> warn
    warn_res = check_yoy("revenue", 140, 100, thresholds)
    assert warn_res["passed"] is True
    assert warn_res["severity"] == "warn"
    assert warn_res["pct_change"] == 40.0


def test_large_spike_and_1000x_unit_error_detection() -> None:
    thresholds = load_thresholds()
    spike = check_yoy(
        "revenue",
        182,
        100,
        thresholds,
        source_snippet="Consolidated Revenue rose due to acquisition.",
    )
    assert spike["passed"] is False
    assert spike["severity"] == "fail"
    assert spike["pct_change"] == 82.0
    assert spike["anomaly_type"] == "large_change"

    # 1000x corrupted extraction must be distinguished as likely unit/scale error
    corrupted = check_yoy("total_assets", 350000000, 350000, thresholds)
    assert corrupted["passed"] is False
    assert corrupted["severity"] == "fail"
    assert corrupted["anomaly_type"] == "likely_unit_error"
    assert "unit/scale" in corrupted["reason"].lower()
    assert "unit" in (corrupted["suggested_action"] or "").lower()


def test_sign_flip_always_flagged() -> None:
    thresholds = load_thresholds()
    # Profit (+100) to loss (-20): -120% < 150% net_income threshold, MUST STILL FAIL!
    flip = check_yoy("net_income", -20, 100, thresholds)
    assert flip["passed"] is False
    assert flip["severity"] == "fail"
    assert flip["anomaly_type"] == "sign_flip"
    assert "profit to loss" in flip["reason"].lower()

    # Loss (-50) to profit (+20): +140% < 150% threshold, MUST STILL FAIL!
    flip_up = check_yoy("net_income", 20, -50, thresholds)
    assert flip_up["passed"] is False
    assert flip_up["severity"] == "fail"
    assert flip_up["anomaly_type"] == "sign_flip"


def test_prior_zero_and_missing_return_not_applicable() -> None:
    thresholds = load_thresholds()
    zero_res = check_yoy("revenue", 100, 0, thresholds)
    assert zero_res["status"] == "not_applicable"
    assert zero_res["passed"] is False
    assert zero_res["pct_change"] is None
    assert "zero" in zero_res["reason"].lower()

    none_res = check_yoy("revenue", 100, None, thresholds)
    assert none_res["status"] == "not_applicable"
    assert none_res["passed"] is False
    assert none_res["pct_change"] is None


def test_negative_prior_uses_abs_denominator() -> None:
    thresholds = load_thresholds()
    # Prior = -100, Current = -50 => (-50 - (-100)) / abs(-100) * 100 = +50%
    res = check_yoy("net_income", -50, -100, thresholds)
    assert res["passed"] is True
    assert res["pct_change"] == 50.0
    assert res["abs_change"] == 50.0


def test_unit_and_period_mismatch_return_not_applicable() -> None:
    thresholds = load_thresholds()
    unit_mismatch = check_yoy(
        "revenue",
        100,
        90,
        thresholds,
        current_unit="USD millions",
        prior_unit="USD thousands",
    )
    assert unit_mismatch["status"] == "not_applicable"
    assert unit_mismatch["passed"] is False
    assert "unit mismatch" in unit_mismatch["reason"].lower()

    period_mismatch = check_yoy(
        "revenue",
        100,
        90,
        thresholds,
        current_period=2024,
        prior_period=2025,
    )
    assert period_mismatch["status"] == "not_applicable"
    assert period_mismatch["passed"] is False


def test_threshold_override() -> None:
    # Default revenue override in thresholds.yaml is 50%; custom override to 15%
    custom = {"default_pct": 40, "metrics": {"revenue": 15}}
    res = check_yoy("revenue", 125, 100, custom)
    assert res["passed"] is False
    assert res["threshold_used"] == 15.0
    assert res["severity"] == "fail"


def test_multi_period_anomaly_zscore() -> None:
    # Historical growth ~5% each year, then sudden jump to +100%
    periods = [
        {"period": 2021, "value": 100.0, "unit": "USD millions"},
        {"period": 2022, "value": 105.0, "unit": "USD millions"},
        {"period": 2023, "value": 110.25, "unit": "USD millions"},
        {"period": 2024, "value": 115.76, "unit": "USD millions"},
        {"period": 2025, "value": 231.52, "unit": "USD millions"},
    ]
    res = check_yoy_multi("revenue", periods)
    assert res["method"] == "zscore"
    assert res["passed"] is False
    assert res["severity"] == "fail"
    assert abs(res["z_score"]) > 2.0
    assert len(res["yoy_series"]) == 4

    # Normal continuation stays passing
    normal_periods = periods[:-1] + [{"period": 2025, "value": 121.55, "unit": "USD millions"}]
    ok_res = check_yoy_multi("revenue", normal_periods)
    assert ok_res["method"] == "zscore"
    assert ok_res["passed"] is True


def test_explain_template_and_json_serializable() -> None:
    thresholds = load_thresholds()
    res = check_yoy("revenue", 182, 100, thresholds)
    text = explain(res)
    assert "82.0%" in text
    assert "50.0%" in text
    # Must be cleanly JSON-serializable for SQLite & React dashboard
    json.dumps(res)


def test_integration_confidence_escalation_and_idempotent_node() -> None:
    thresholds = load_thresholds()
    fail_res = check_yoy(
        "revenue",
        180,
        100,
        thresholds,
        source_snippet="Revenue jumped from $100M to $180M.",
    )
    warn_res = check_yoy("revenue", 140, 100, thresholds)
    ok_res = check_yoy("revenue", 105, 100, thresholds)

    assert apply_yoy_to_confidence({"confidence": 0.9}, fail_res) == 0.54
    assert apply_yoy_to_confidence({"confidence": 0.9}, warn_res) == 0.765
    assert apply_yoy_to_confidence({"confidence": 0.9}, ok_res) == 0.9

    esc = to_escalation(fail_res)
    assert esc is not None
    assert esc["metric"] == "revenue"
    assert esc["severity"] == "fail"
    assert esc["source_snippet"] == "Revenue jumped from $100M to $180M."
    assert to_escalation(ok_res) is None
    assert to_escalation(warn_res) is None

    state = {
        "company": "TestCorp",
        "metrics": {
            "revenue": {
                "current_value": 180,
                "prior_value": 100,
                "unit": "USD millions",
                "current_period": 2025,
                "prior_period": 2024,
                "confidence": 0.9,
                "source_snippet": "Revenue $180M vs $100M.",
            },
            "net_income": {
                "current_value": 22,
                "prior_value": 20,
                "unit": "USD millions",
                "current_period": 2025,
                "prior_period": 2024,
                "confidence": 0.95,
                "source_snippet": "Net income $22M vs $20M.",
            },
        },
    }

    out1 = yoy_validation_node(state)
    assert len(out1["validation_results"]) == 2
    assert len(out1["escalations"]) == 1

    # Running again on updated state must be completely idempotent (no duplicates!)
    state_after = {**state, **out1}
    out2 = yoy_validation_node(state_after)
    assert out2["validation_results"] == out1["validation_results"]
    assert out2["escalations"] == out1["escalations"]

    # analyze_company smoke test
    company_results = analyze_company(state["metrics"], thresholds, company="TestCorp")
    assert len(company_results) == 2
