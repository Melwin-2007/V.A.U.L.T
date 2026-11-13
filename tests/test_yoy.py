"""Pytest and unittest suite for the Year-on-Year (YoY) Financial Validation module."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from YOY_model import (
    analyze_company,
    apply_yoy_to_confidence,
    check_yoy,
    check_yoy_multi,
    explain,
    load_thresholds,
    to_escalation,
    yoy_validation_node,
)


class TestYoYValidation(unittest.TestCase):
    def test_normal_change_ok_and_warn(self) -> None:
        thresholds = load_thresholds()
        ok_res = check_yoy("revenue", 110, 100, thresholds)
        self.assertEqual(ok_res["check"], "yoy_change")
        self.assertTrue(ok_res["passed"])
        self.assertEqual(ok_res["severity"], "ok")
        self.assertEqual(ok_res["pct_change"], 10.0)
        self.assertEqual(ok_res["abs_change"], 10.0)
        self.assertEqual(ok_res["threshold_used"], 50.0)

        # 40% is >= 75% of 50% (37.5%) and <= 50% -> warn
        warn_res = check_yoy("revenue", 140, 100, thresholds)
        self.assertTrue(warn_res["passed"])
        self.assertEqual(warn_res["severity"], "warn")
        self.assertEqual(warn_res["pct_change"], 40.0)

    def test_large_spike_and_1000x_unit_error_detection(self) -> None:
        thresholds = load_thresholds()
        spike = check_yoy(
            "revenue",
            182,
            100,
            thresholds,
            source_snippet="Consolidated Revenue rose due to acquisition.",
        )
        self.assertFalse(spike["passed"])
        self.assertEqual(spike["severity"], "fail")
        self.assertEqual(spike["pct_change"], 82.0)
        self.assertEqual(spike["anomaly_type"], "large_change")

        # 1000x corrupted extraction must be distinguished as likely unit/scale error
        corrupted = check_yoy("total_assets", 350000000, 350000, thresholds)
        self.assertFalse(corrupted["passed"])
        self.assertEqual(corrupted["severity"], "fail")
        self.assertEqual(corrupted["anomaly_type"], "likely_unit_error")
        self.assertIn("unit/scale", corrupted["reason"].lower())
        self.assertIn("unit", (corrupted["suggested_action"] or "").lower())

    def test_sign_flip_always_flagged(self) -> None:
        thresholds = load_thresholds()
        # Profit (+100) to loss (-20): -120% < 150% net_income threshold, MUST STILL FAIL!
        flip = check_yoy("net_income", -20, 100, thresholds)
        self.assertFalse(flip["passed"])
        self.assertEqual(flip["severity"], "fail")
        self.assertEqual(flip["anomaly_type"], "sign_flip")
        self.assertIn("profit to loss", flip["reason"].lower())

        # Loss (-50) to profit (+20): +140% < 150% threshold, MUST STILL FAIL!
        flip_up = check_yoy("net_income", 20, -50, thresholds)
        self.assertFalse(flip_up["passed"])
        self.assertEqual(flip_up["severity"], "fail")
        self.assertEqual(flip_up["anomaly_type"], "sign_flip")

    def test_prior_zero_and_missing_return_not_applicable(self) -> None:
        thresholds = load_thresholds()
        zero_res = check_yoy("revenue", 100, 0, thresholds)
        self.assertEqual(zero_res["status"], "not_applicable")
        self.assertFalse(zero_res["passed"])
        self.assertIsNone(zero_res["pct_change"])
        self.assertIn("zero", zero_res["reason"].lower())

        none_res = check_yoy("revenue", 100, None, thresholds)
        self.assertEqual(none_res["status"], "not_applicable")
        self.assertFalse(none_res["passed"])
        self.assertIsNone(none_res["pct_change"])

    def test_negative_prior_uses_abs_denominator(self) -> None:
        thresholds = load_thresholds()
        # Prior = -100, Current = -50 => (-50 - (-100)) / abs(-100) * 100 = +50%
        res = check_yoy("net_income", -50, -100, thresholds)
        self.assertTrue(res["passed"])
        self.assertEqual(res["pct_change"], 50.0)
        self.assertEqual(res["abs_change"], 50.0)

    def test_unit_and_period_mismatch_return_not_applicable(self) -> None:
        thresholds = load_thresholds()
        unit_mismatch = check_yoy(
            "revenue",
            100,
            90,
            thresholds,
            current_unit="USD millions",
            prior_unit="USD thousands",
        )
        self.assertEqual(unit_mismatch["status"], "not_applicable")
        self.assertFalse(unit_mismatch["passed"])
        self.assertIn("unit mismatch", unit_mismatch["reason"].lower())

        period_mismatch = check_yoy(
            "revenue",
            100,
            90,
            thresholds,
            current_period=2024,
            prior_period=2025,
        )
        self.assertEqual(period_mismatch["status"], "not_applicable")
        self.assertFalse(period_mismatch["passed"])

    def test_threshold_override(self) -> None:
        custom = {"default_pct": 40, "metrics": {"revenue": 15}}
        res = check_yoy("revenue", 125, 100, custom)
        self.assertFalse(res["passed"])
        self.assertEqual(res["threshold_used"], 15.0)
        self.assertEqual(res["severity"], "fail")

    def test_multi_period_anomaly_zscore(self) -> None:
        periods = [
            {"period": 2021, "value": 100.0, "unit": "USD millions"},
            {"period": 2022, "value": 105.0, "unit": "USD millions"},
            {"period": 2023, "value": 110.25, "unit": "USD millions"},
            {"period": 2024, "value": 115.76, "unit": "USD millions"},
            {"period": 2025, "value": 231.52, "unit": "USD millions"},
        ]
        res = check_yoy_multi("revenue", periods)
        self.assertEqual(res["method"], "zscore")
        self.assertFalse(res["passed"])
        self.assertEqual(res["severity"], "fail")
        self.assertGreater(abs(res["z_score"]), 2.0)
        self.assertEqual(len(res["yoy_series"]), 4)

        normal_periods = periods[:-1] + [{"period": 2025, "value": 121.55, "unit": "USD millions"}]
        ok_res = check_yoy_multi("revenue", normal_periods)
        self.assertEqual(ok_res["method"], "zscore")
        self.assertTrue(ok_res["passed"])

    def test_explain_template_and_json_serializable(self) -> None:
        thresholds = load_thresholds()
        res = check_yoy("revenue", 182, 100, thresholds)
        text = explain(res)
        self.assertIn("82.0%", text)
        self.assertIn("50.0%", text)
        json.dumps(res)

    def test_integration_confidence_escalation_and_idempotent_node(self) -> None:
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

        self.assertEqual(apply_yoy_to_confidence({"confidence": 0.9}, fail_res), 0.54)
        self.assertEqual(apply_yoy_to_confidence({"confidence": 0.9}, warn_res), 0.765)
        self.assertEqual(apply_yoy_to_confidence({"confidence": 0.9}, ok_res), 0.9)

        esc = to_escalation(fail_res)
        self.assertIsNotNone(esc)
        self.assertEqual(esc["metric"], "revenue")
        self.assertEqual(esc["severity"], "fail")
        self.assertEqual(esc["source_snippet"], "Revenue jumped from $100M to $180M.")
        self.assertIsNone(to_escalation(ok_res))
        self.assertIsNone(to_escalation(warn_res))

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
        self.assertEqual(len(out1["validation_results"]), 2)
        self.assertEqual(len(out1["escalations"]), 1)

        state_after = {**state, **out1}
        out2 = yoy_validation_node(state_after)
        self.assertEqual(out2["validation_results"], out1["validation_results"])
        self.assertEqual(out2["escalations"], out1["escalations"])

        company_results = analyze_company(state["metrics"], thresholds, company="TestCorp")
        self.assertEqual(len(company_results), 2)


if __name__ == "__main__":
    unittest.main()
