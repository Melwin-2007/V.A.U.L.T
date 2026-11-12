from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))

from segmentation.cli import main as cli_main
from segmentation.confidence import compute_confidence, validate_metric
from segmentation.extractor import (
    ExtractedFilingData,
    HtmlTableBlock,
    RawMetricFact,
    RawSegmentFact,
)
from segmentation.models import UnitContext
from segmentation.validator import evaluate_extracted_data, validate_xbrl_directory


class TestConfidenceAndEscalationRules(unittest.TestCase):
    def test_should_compute_example_a_auto_approved_score_1_00(self) -> None:
        unit_ctx = UnitContext(
            unit_label="USD millions",
            scale_divisor=1e6,
            fiscal_period_detected=True,
            unit_scale_detected=True,
            fiscal_label="2025",
            header_snippet="Year Ended 2025 (in millions)",
        )
        result = validate_metric(
            metric="consolidated_revenue",
            label="Total net sales",
            raw_value=391_035_000_000.0,
            prior_value=383_285_000_000.0,  # +2.02% YoY <= 25%
            period="FY2025",
            source_section="Consolidated Statements of Operations",
            source_snippet="Total net sales: $391,035",
            unit_ctx=unit_ctx,
            s_label=1.0,
            is_derived=False,
        )
        self.assertEqual(result.confidence.score, 1.0)
        self.assertEqual(result.decision, "AUTO-APPROVED")
        self.assertFalse(result.hallucination_flag)

    def test_should_compute_example_b_derived_total_liabilities_score_0_50_and_escalate(self) -> None:
        unit_ctx = UnitContext(
            unit_label="USD millions",
            scale_divisor=1e6,
            fiscal_period_detected=True,
            unit_scale_detected=True,
            fiscal_label="2025",
            header_snippet="As of 2025 (in millions)",
        )
        result = validate_metric(
            metric="total_liabilities",
            label="Total liabilities (Current + Non-current)",
            raw_value=308_030_000_000.0,
            prior_value=293_360_000_000.0,  # +5.0% YoY <= 25%
            period="As of 2025",
            source_section="Consolidated Balance Sheets",
            source_snippet="Current liabilities | 150,000 | Non-current liabilities | 158,030",
            unit_ctx=unit_ctx,
            s_label=0.6,
            is_derived=True,
        )
        self.assertEqual(result.confidence.s_grounding, 0.0)
        self.assertEqual(result.confidence.s_label, 0.6)
        self.assertEqual(result.confidence.s_unit, 1.0)
        self.assertEqual(result.confidence.s_yoy, 1.0)
        self.assertEqual(result.confidence.score, 0.5)
        self.assertEqual(result.decision, "HUMAN_REVIEW")
        self.assertTrue(any("derived" in r.lower() for r in result.escalation_reasons))

    def test_should_escalate_all_balance_sheet_metrics_on_example_c_accounting_equation_failure(self) -> None:
        tables = [
            HtmlTableBlock(
                section="Income Statement",
                header_text="Year Ended 2025 (in millions)",
                rows=["Total net sales | $ 391,035"],
            ),
            HtmlTableBlock(
                section="Balance Sheet",
                header_text="As of 2025 (in millions)",
                rows=[
                    "Total assets | $ 364,980",
                    "Total liabilities | $ 308,030",
                    "Total shareholders' equity | $ 66,950",  # Typo: 308,030 + 66,950 = 374,980 != 364,980
                ],
            ),
            HtmlTableBlock(
                section="Note - Segment Information",
                header_text="Year Ended 2025 (in millions)",
                rows=[
                    "iPhone | $ 201,183",
                    "Mac | $ 29,984",
                    "iPad | $ 26,694",
                    "Wearables, Home & Accessories | $ 37,005",
                    "Services | $ 96,169",
                ],
            ),
        ]
        data = ExtractedFilingData(
            cik="0000320193",
            company_name="Apple Inc. (Synthetic Typo)",
            form="10-K",
            accession_no="0000320193-25-000001",
            filing_date="2025-11-01",
            period_of_report="2025-09-27",
            sec_url="https://www.sec.gov/example",
            tables=tables,
            consolidated_revenue=RawMetricFact(
                metric="consolidated_revenue",
                label="Total net sales",
                concept="us-gaap:Revenues",
                current_value=391_035_000_000.0,
                prior_value=383_285_000_000.0,
                period_label="FY2025",
                expected_year="2025",
                fallback_scale=1e6,
                is_derived=False,
                s_label=1.0,
                source_section_hint="Income Statement",
                label_keywords=["total net sales"],
            ),
            total_assets=RawMetricFact(
                metric="total_assets",
                label="Total assets",
                concept="us-gaap:Assets",
                current_value=364_980_000_000.0,
                prior_value=352_583_000_000.0,
                period_label="As of 2025",
                expected_year="2025",
                fallback_scale=1e6,
                is_derived=False,
                s_label=1.0,
                source_section_hint="Balance Sheet",
                label_keywords=["total assets"],
            ),
            total_liabilities=RawMetricFact(
                metric="total_liabilities",
                label="Total liabilities",
                concept="us-gaap:Liabilities",
                current_value=308_030_000_000.0,
                prior_value=290_437_000_000.0,
                period_label="As of 2025",
                expected_year="2025",
                fallback_scale=1e6,
                is_derived=False,
                s_label=1.0,
                source_section_hint="Balance Sheet",
                label_keywords=["total liabilities"],
            ),
            stockholders_equity=RawMetricFact(
                metric="stockholders_equity",
                label="Total shareholders' equity",
                concept="us-gaap:StockholdersEquity",
                current_value=66_950_000_000.0,
                prior_value=62_146_000_000.0,
                period_label="As of 2025",
                expected_year="2025",
                fallback_scale=1e6,
                is_derived=False,
                s_label=1.0,
                source_section_hint="Balance Sheet",
                label_keywords=["total shareholders' equity"],
            ),
            is_single_segment=False,
            single_segment_reason="",
            axis_used="ProductOrServiceAxis",
            breakdown_type="Product / Service Segments",
            segment_items=[
                RawSegmentFact("iPhone", "segment", "ProductOrServiceAxis", "iPhone", "Revenues", 201_183e6, 200_000e6, 201_183e6, "FY2025", "2025", 1e6, 1.0),
                RawSegmentFact("Mac", "segment", "ProductOrServiceAxis", "Mac", "Revenues", 29_984e6, 29_000e6, 29_984e6, "FY2025", "2025", 1e6, 1.0),
                RawSegmentFact("iPad", "segment", "ProductOrServiceAxis", "iPad", "Revenues", 26_694e6, 27_000e6, 26_694e6, "FY2025", "2025", 1e6, 1.0),
                RawSegmentFact("Wearables, Home & Accessories", "segment", "ProductOrServiceAxis", "Wearables", "Revenues", 37_005e6, 38_000e6, 37_005e6, "FY2025", "2025", 1e6, 1.0),
                RawSegmentFact("Services", "segment", "ProductOrServiceAxis", "Services", "Revenues", 96_169e6, 90_000e6, 96_169e6, "FY2025", "2025", 1e6, 1.0),
            ],
        )

        report = evaluate_extracted_data(data)
        self.assertEqual(report.segment_reconciliation.status, "PASS")
        self.assertFalse(report.accounting_equation.passed)
        self.assertEqual(report.verdict, "FAIL")
        for key in ("total_assets", "total_liabilities", "stockholders_equity"):
            self.assertEqual(report.accounting_equation.metrics[key].confidence.score, 1.0)
            self.assertEqual(report.accounting_equation.metrics[key].decision, "HUMAN_REVIEW")

    def test_should_fail_segment_reconciliation_when_segment_digit_misread(self) -> None:
        tables = [
            HtmlTableBlock("Income", "Year Ended 2025 (in millions)", ["Total net sales | $ 391,035"]),
            HtmlTableBlock("Balance", "As of 2025 (in millions)", ["Total assets | $ 364,980", "Total liabilities | $ 308,030", "Total shareholders' equity | $ 56,950"]),
            HtmlTableBlock(
                "Note",
                "Year Ended 2025 (in millions)",
                [
                    "iPhone | $ 201,183",
                    "Mac | $ 29,984",
                    "iPad | $ 26,694",
                    "Wearables, Home & Accessories | $ 37,005",
                    "Services | $ 96,169",  # Real value in HTML is 96,169, AI misread as 69,169
                ],
            ),
        ]
        data = ExtractedFilingData(
            cik="0000320193",
            company_name="Apple Inc. (Misread Services)",
            form="10-K",
            accession_no="test",
            filing_date="2025-11-01",
            period_of_report="2025-09-27",
            sec_url="",
            tables=tables,
            consolidated_revenue=RawMetricFact("consolidated_revenue", "Total net sales", "Revenues", 391_035e6, 383_285e6, "FY2025", "2025", 1e6, False, 1.0, "Income", ["total net sales"]),
            total_assets=RawMetricFact("total_assets", "Total assets", "Assets", 364_980e6, 352_583e6, "2025", "2025", 1e6, False, 1.0, "Balance", ["total assets"]),
            total_liabilities=RawMetricFact("total_liabilities", "Total liabilities", "Liabilities", 308_030e6, 290_437e6, "2025", "2025", 1e6, False, 1.0, "Balance", ["total liabilities"]),
            stockholders_equity=RawMetricFact("stockholders_equity", "Total shareholders' equity", "Equity", 56_950e6, 62_146e6, "2025", "2025", 1e6, False, 1.0, "Balance", ["total shareholders' equity"]),
            is_single_segment=False,
            single_segment_reason="",
            axis_used="ProductOrServiceAxis",
            breakdown_type="Product / Service Segments",
            segment_items=[
                RawSegmentFact("iPhone", "segment", "ProductOrServiceAxis", "iPhone", "Revenues", 201_183e6, 200_000e6, 201_183e6, "FY2025", "2025", 1e6, 1.0),
                RawSegmentFact("Mac", "segment", "ProductOrServiceAxis", "Mac", "Revenues", 29_984e6, 29_000e6, 29_984e6, "FY2025", "2025", 1e6, 1.0),
                RawSegmentFact("iPad", "segment", "ProductOrServiceAxis", "iPad", "Revenues", 26_694e6, 27_000e6, 26_694e6, "FY2025", "2025", 1e6, 1.0),
                RawSegmentFact("Wearables, Home & Accessories", "segment", "ProductOrServiceAxis", "Wearables", "Revenues", 37_005e6, 38_000e6, 37_005e6, "FY2025", "2025", 1e6, 1.0),
                RawSegmentFact("Services", "segment", "ProductOrServiceAxis", "Services", "Revenues", 69_169e6, 65_000e6, 69_169e6, "FY2025", "2025", 1e6, 1.0),
            ],
        )

        report = evaluate_extracted_data(data)
        self.assertEqual(report.segment_reconciliation.status, "FAIL")
        self.assertEqual(report.verdict, "FAIL")
        self.assertEqual(report.segment_reconciliation.items[-1].validation.confidence.s_grounding, 0.0)
        self.assertTrue(report.segment_reconciliation.items[-1].validation.hallucination_flag)

    def test_should_apply_formula_weights_accurately(self) -> None:
        breakdown = compute_confidence(s_grounding=1.0, s_label=0.6, s_unit=0.5, s_yoy=0.3)
        expected = round((0.40 * 1.0) + (0.25 * 0.6) + (0.20 * 0.5) + (0.15 * 0.3), 4)
        self.assertEqual(breakdown.score, expected)

    def test_should_ground_corporate_zero_dash_cell_and_pass_reconciliation(self) -> None:
        tables = [
            HtmlTableBlock(
                section="Income Statement",
                header_text="Year Ended Sep 26, 2026 (in millions)",
                rows=["Net sales | $ 478,834"],
            ),
            HtmlTableBlock(
                section="Balance Sheet",
                header_text="As of Sep 26, 2026 (in millions)",
                rows=[
                    "Total assets | $ 404,548",
                    "Total liabilities | $ 292,455",
                    "Total shareholders' equity | $ 112,093",
                ],
            ),
            HtmlTableBlock(
                section="Note - Segment Information",
                header_text="Year Ended Sep 26, 2026 (in millions) | Americas | Europe | Greater China | Japan | Rest of Asia Pacific | Corporate | Total",
                rows=["Net sales | $ 198,083 | $ 127,604 | $ 82,378 | $ 30,908 | $ 39,861 | $ | — | $ 478,834"],
            ),
        ]
        data = ExtractedFilingData(
            cik="0000320193",
            company_name="Apple Inc.",
            form="10-K",
            accession_no="0000320193-26-000026",
            filing_date="2026-11-03",
            period_of_report="2026-09-26",
            sec_url="https://www.sec.gov/example",
            tables=tables,
            consolidated_revenue=RawMetricFact("consolidated_revenue", "Net sales", "Revenues", 478_834e6, 416_161e6, "FY2026", "2026", 1e6, False, 1.0, "Income Statement", ["net sales"]),
            total_assets=RawMetricFact("total_assets", "Total assets", "Assets", 404_548e6, 359_241e6, "As of 2026", "2026", 1e6, False, 1.0, "Balance Sheet", ["total assets"]),
            total_liabilities=RawMetricFact("total_liabilities", "Total liabilities", "Liabilities", 292_455e6, 285_508e6, "As of 2026", "2026", 1e6, False, 1.0, "Balance Sheet", ["total liabilities"]),
            stockholders_equity=RawMetricFact("stockholders_equity", "Total shareholders' equity", "Equity", 112_093e6, 73_733e6, "As of 2026", "2026", 1e6, False, 1.0, "Balance Sheet", ["total shareholders' equity"]),
            is_single_segment=False,
            single_segment_reason="",
            axis_used="StatementBusinessSegmentsAxis",
            breakdown_type="Business / Operating Segments",
            segment_items=[
                RawSegmentFact("Americas", "segment", "StatementBusinessSegmentsAxis", "Americas", "Revenues", 198_083e6, 178_353e6, 198_083e6, "FY2026", "2026", 1e6, 1.0),
                RawSegmentFact("Europe", "segment", "StatementBusinessSegmentsAxis", "Europe", "Revenues", 127_604e6, 111_025e6, 127_604e6, "FY2026", "2026", 1e6, 1.0),
                RawSegmentFact("Greater China", "segment", "StatementBusinessSegmentsAxis", "Greater China", "Revenues", 82_378e6, 64_377e6, 82_378e6, "FY2026", "2026", 1e6, 1.0),
                RawSegmentFact("Japan", "segment", "StatementBusinessSegmentsAxis", "Japan", "Revenues", 30_908e6, 28_703e6, 30_908e6, "FY2026", "2026", 1e6, 1.0),
                RawSegmentFact("Rest of Asia Pacific", "segment", "StatementBusinessSegmentsAxis", "Rest of Asia Pacific", "Revenues", 39_861e6, 33_695e6, 39_861e6, "FY2026", "2026", 1e6, 1.0),
                RawSegmentFact("Corporate non-segment", "corporate_other", "ConsolidationItemsAxis", "us-gaap:CorporateNonSegmentMember", "Revenues", 0.0, None, 0.0, "FY2026", "2026", 1e6, 1.0),
            ],
        )

        report = evaluate_extracted_data(data)
        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.segment_reconciliation.status, "PASS")
        corp_item = report.segment_reconciliation.items[-1]
        self.assertEqual(corp_item.validation.confidence.score, 1.0)
        self.assertEqual(corp_item.validation.decision, "AUTO-APPROVED")

    def test_should_not_treat_negative_dollar_amount_as_zero_dash_placeholder(self) -> None:
        from segmentation.confidence import has_accounting_zero_marker

        self.assertFalse(has_accounting_zero_marker("Eliminations | $-1,869 | $ 45,754"))
        self.assertTrue(has_accounting_zero_marker("Corporate | $— | $ 478,834"))


class TestRealXbrlFixturesIntegration(unittest.TestCase):
    FIXTURES = _WORKSPACE_ROOT / "edgartools" / "tests" / "fixtures" / "xbrl"

    def test_aapl_10k_2023_passes_segment_and_accounting_validation(self) -> None:
        report = validate_xbrl_directory(self.FIXTURES / "aapl" / "10k_2023", cik="0000320193")
        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.segment_reconciliation.status, "PASS")
        self.assertEqual(report.segment_reconciliation.difference, 0.0)
        self.assertTrue(report.accounting_equation.passed)

    def test_msft_10k_2024_passes_segment_and_accounting_validation(self) -> None:
        report = validate_xbrl_directory(self.FIXTURES / "msft" / "10k_2024", cik="0000789019")
        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.segment_reconciliation.status, "PASS")
        self.assertEqual(report.segment_reconciliation.difference, 0.0)
        self.assertTrue(report.accounting_equation.passed)

    def test_nvda_10k_2026_passes_despite_high_yoy_growth(self) -> None:
        report = validate_xbrl_directory(self.FIXTURES / "nvda" / "10k_2026", cik="0001045810")
        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.segment_reconciliation.status, "PASS")
        self.assertEqual(report.consolidated_revenue.confidence.s_yoy, 0.3)
        self.assertEqual(report.consolidated_revenue.confidence.score, 0.895)

    def test_nflx_10k_2024_detects_single_operating_segment_cleanly(self) -> None:
        report = validate_xbrl_directory(self.FIXTURES / "nflx" / "10k_2024", cik="0001065280")
        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.segment_reconciliation.status, "N/A - Single Segment")
        self.assertTrue(report.accounting_equation.passed)

    def test_ko_10k_2024_reconciles_corporate_and_eliminations_and_flags_derived_liabilities(self) -> None:
        report = validate_xbrl_directory(self.FIXTURES / "ko" / "10k_2024", cik="0000021344")
        self.assertEqual(report.segment_reconciliation.status, "PASS")
        self.assertGreater(report.segment_reconciliation.corporate_other, 0.0)
        self.assertGreater(report.segment_reconciliation.intersegment_eliminations, 0.0)
        self.assertEqual(report.segment_reconciliation.difference, 0.0)
        self.assertTrue(report.accounting_equation.passed)
        liab = report.accounting_equation.metrics["total_liabilities"]
        self.assertEqual(liab.decision, "HUMAN_REVIEW")
        self.assertEqual(liab.confidence.s_grounding, 0.0)
        self.assertEqual(liab.confidence.s_label, 0.6)

    def test_cli_writes_valid_json_report_in_one_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "aapl_seg.json"
            exit_code = cli_main(
                [
                    "0000320193",
                    "--xbrl-dir",
                    str(self.FIXTURES / "aapl" / "10k_2023"),
                    "--output",
                    str(out_file),
                    "--strict-exit",
                ]
            )
            self.assertEqual(exit_code, 0)
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["verdict"], "PASS")
            self.assertEqual(payload["segment_reconciliation"]["status"], "PASS")

    def test_cli_simulate_anomaly_produces_valid_flawed_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            out_file = Path(tmpdir) / "aapl_anomaly.json"
            exit_code = cli_main(
                [
                    "0000320193",
                    "--xbrl-dir",
                    str(self.FIXTURES / "aapl" / "10k_2023"),
                    "--simulate-anomaly",
                    "accounting-equation",
                    "--output",
                    str(out_file),
                    "--strict-exit",
                ]
            )
            self.assertEqual(exit_code, 2)
            payload = json.loads(out_file.read_text(encoding="utf-8"))
            self.assertEqual(payload["verdict"], "FAIL")
            self.assertFalse(payload["accounting_equation"]["passed"])
            for k in ("total_assets", "total_liabilities", "stockholders_equity"):
                self.assertEqual(payload["accounting_equation"]["metrics"][k]["decision"], "HUMAN_REVIEW")


if __name__ == "__main__":
    unittest.main()

