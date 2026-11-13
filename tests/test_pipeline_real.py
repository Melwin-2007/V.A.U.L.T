"""Unit and integration test suite for V.A.U.L.T Real Data Pipeline (Member A)."""

import os
import sys
import unittest

# Ensure workspace is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pipeline.accuracy import build_accuracy_report
from pipeline.edgar_client import COMPANY_MAP, fetch_filing
from pipeline.error_injection import apply_error_injection
from pipeline.extractor import extract_metrics
from pipeline.ground_truth import fetch_ground_truth
from pipeline.scoring import score_metrics
from pipeline.validators import run_validations


class TestRealPipeline(unittest.TestCase):
    """Test suite covering SEC client, extractor, scorer, validators, error injection, and accuracy."""

    def test_fetch_filing_all_5_companies(self):
        """All 5 target companies must return valid 10-K filing metadata."""
        for cik in COMPANY_MAP.keys():
            filing = fetch_filing(cik)
            self.assertEqual(filing["cik"], cik)
            self.assertEqual(filing["form"], "10-K")
            self.assertIn(filing["source"], ("edgar_live", "offline_fixture", "offline_synthetic"))
            self.assertTrue(len(filing["accession_no"]) > 5)

    def test_extraction_10_metrics_apple(self):
        """Apple FY2023 must extract exactly 10 financial metrics with verbatim snippets."""
        filing = fetch_filing("0000320193")
        filing["run_id"] = "test_run_aapl"
        records = extract_metrics(filing)
        self.assertEqual(len(records), 10)

        by_name = {r["metric_name"]: r for r in records}
        for expected in [
            "revenue", "net_income", "total_assets", "total_liabilities",
            "shareholders_equity", "operating_cash_flow", "current_assets",
            "current_liabilities", "cash_and_equivalents", "diluted_eps"
        ]:
            self.assertIn(expected, by_name)
            self.assertIsNotNone(by_name[expected]["value"])

        # Mathematical accounting identity verification: A = L + E
        assets = by_name["total_assets"]["value"]
        liab = by_name["total_liabilities"]["value"]
        equity = by_name["shareholders_equity"]["value"]
        self.assertEqual(assets, liab + equity)

    def test_extraction_coca_cola_derived_liabilities(self):
        """Coca-Cola lacks direct Total Liabilities tag; engine must derive it and set is_derived=True."""
        filing = fetch_filing("0000021344")
        filing["run_id"] = "test_run_ko"
        records = extract_metrics(filing)
        by_name = {r["metric_name"]: r for r in records}

        self.assertIn("total_liabilities", by_name)
        liab_rec = by_name["total_liabilities"]
        self.assertTrue(liab_rec["is_derived"])
        self.assertIsNotNone(liab_rec["value"])

        # Identity verification: A == L + E
        assets = by_name["total_assets"]["value"]
        liab = liab_rec["value"]
        equity = by_name["shareholders_equity"]["value"]
        self.assertAlmostEqual(assets, liab + equity, delta=1000.0)

    def test_confidence_scoring_clean_run(self):
        """Clean Apple extraction must earn high confidence and auto-approval."""
        filing = fetch_filing("0000320193")
        filing["run_id"] = "test_run_aapl_score"
        records = extract_metrics(filing)
        scored = score_metrics(records)

        auto_approved = [r for r in scored if r["status"] == "AUTO_APPROVED"]
        self.assertGreaterEqual(len(auto_approved), 9)

    def test_validation_catches_scale_1000x(self):
        """Scale 1000x error injection on revenue must trigger Trigger B hard failure."""
        filing = fetch_filing("0000320193")
        records = extract_metrics(filing)
        corrupted = apply_error_injection(records, "scale_1000x")
        scored = score_metrics(corrupted)
        validated, checks = run_validations(scored)

        rev = next(r for r in validated if r["metric_name"] == "revenue")
        self.assertEqual(rev["status"], "NEEDS_REVIEW")
        scale_checks = [c for c in checks if c["check_name"] == "scale_error"]
        self.assertEqual(len(scale_checks), 1)
        self.assertFalse(scale_checks[0]["passed"])
        self.assertEqual(scale_checks[0]["severity"], "hard")

    def test_validation_catches_sign_flip(self):
        """Sign flip on net income must trigger Trigger B hard failure."""
        filing = fetch_filing("0000320193")
        records = extract_metrics(filing)
        corrupted = apply_error_injection(records, "sign_flip")
        scored = score_metrics(corrupted)
        validated, checks = run_validations(scored)

        ni = next(r for r in validated if r["metric_name"] == "net_income")
        self.assertEqual(ni["status"], "NEEDS_REVIEW")
        flip_checks = [c for c in checks if c["check_name"] == "sign_flip"]
        self.assertEqual(len(flip_checks), 1)
        self.assertFalse(flip_checks[0]["passed"])

    def test_validation_catches_missing_digit(self):
        """Missing digit must fail verbatim source grounding and drop confidence (Trigger A)."""
        filing = fetch_filing("0000320193")
        records = extract_metrics(filing)
        corrupted = apply_error_injection(records, "missing_digit")
        scored = score_metrics(corrupted)
        validated, checks = run_validations(scored)

        assets = next(r for r in validated if r["metric_name"] == "total_assets")
        self.assertEqual(assets["status"], "NEEDS_REVIEW")
        self.assertLess(assets["confidence"], 0.85)

    def test_ground_truth_accuracy_report(self):
        """Accuracy report must achieve 100% extraction accuracy on clean Apple run."""
        filing = fetch_filing("0000320193")
        records = extract_metrics(filing)
        scored = score_metrics(records)
        validated, checks = run_validations(scored)

        gt = fetch_ground_truth("0000320193")
        report = build_accuracy_report(validated, gt)

        self.assertEqual(report["overall_accuracy_pct"], 100.0)
        self.assertEqual(len(report["missed_errors"]), 0)
        self.assertEqual(report["auto_approved_accuracy_pct"], 100.0)
        self.assertEqual(report["escalation_precision"], 100.0)


if __name__ == "__main__":
    unittest.main()

