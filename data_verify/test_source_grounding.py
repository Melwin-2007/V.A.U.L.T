from pathlib import Path
import sys
import unittest

_WORKSPACE_ROOT = str(Path(__file__).resolve().parent.parent)
if _WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, _WORKSPACE_ROOT)

from data_verify.source_grounding import (
    build_grounded_field,
    safe_compute_derived,
    verify_source_grounding,
)


class TestSourceGroundingCheck(unittest.TestCase):
    def test_should_pass_grounding_when_exact_digits_exist_verbatim_in_snippet(self) -> None:
        snippet = "Total revenues | $ | 713,163 | $ | 680,985 | $ | 648,125"
        result = verify_source_grounding("713,163", snippet, numeric_value=713163)

        self.assertTrue(result.passed)
        self.assertEqual(result.confidence_score, 1.0)
        self.assertEqual(result.risk_level, "LOW")
        self.assertFalse(result.hallucination_flag)
        self.assertEqual(result.matched_text, "713,163")

    def test_should_drop_confidence_to_zero_and_flag_hallucination_when_digits_missing(self) -> None:
        snippet = "Total revenues | $ | 713,163 | $ | 680,985 | $ | 648,125"
        result = verify_source_grounding("391,035", snippet, numeric_value=391035)

        self.assertFalse(result.passed)
        self.assertEqual(result.confidence_score, 0.0)
        self.assertEqual(result.risk_level, "HIGH")
        self.assertTrue(result.hallucination_flag)
        self.assertIsNone(result.matched_text)

    def test_should_not_match_partial_digits_inside_larger_number(self) -> None:
        snippet = "Operating income | 29,825 | 29,348 | 27,012"
        result = verify_source_grounding("825", snippet, numeric_value=825)

        self.assertFalse(result.passed)
        self.assertEqual(result.confidence_score, 0.0)
        self.assertTrue(result.hallucination_flag)

    def test_should_handle_parenthesized_negative_accounting_numbers(self) -> None:
        snippet = "Payments for property and equipment | (26,642) | (23,783) | (20,606)"
        result = verify_source_grounding("-26,642", snippet, numeric_value=-26642)

        self.assertTrue(result.passed)
        self.assertEqual(result.confidence_score, 1.0)
        self.assertEqual(result.matched_text, "(26,642)")

    def test_should_block_math_when_any_input_fails_source_grounding(self) -> None:
        grounded_ocf = build_grounded_field(
            metric="operating_cash_flow",
            period="FY2026",
            extracted_value=41565,
            display_value="41,565",
            unit="USD millions",
            source_section="Cash Flows",
            source_snippet="Net cash provided by operating activities | 41,565 | 36,443",
        )
        hallucinated_capex = build_grounded_field(
            metric="capital_expenditures",
            period="FY2026",
            extracted_value=99999,
            display_value="99,999",
            unit="USD millions",
            source_section="Cash Flows",
            source_snippet="Payments for property and equipment | (26,642) | (23,783)",
        )

        derived = safe_compute_derived(
            metric="free_cash_flow",
            formula="operating_cash_flow - capital_expenditures",
            unit="USD millions",
            inputs={
                "operating_cash_flow": grounded_ocf,
                "capital_expenditures": hallucinated_capex,
            },
            compute_fn=lambda d: d["operating_cash_flow"] - d["capital_expenditures"],
        )

        self.assertTrue(derived.blocked)
        self.assertIsNone(derived.value)
        self.assertEqual(hallucinated_capex.grounding.confidence_score, 0.0)
        self.assertTrue(hallucinated_capex.grounding.hallucination_flag)

    def test_should_compute_math_when_all_inputs_pass_source_grounding(self) -> None:
        ocf = build_grounded_field(
            metric="operating_cash_flow",
            period="FY2026",
            extracted_value=41565,
            display_value="41,565",
            unit="USD millions",
            source_section="Cash Flows",
            source_snippet="Net cash provided by operating activities | 41,565 | 36,443",
        )
        capex = build_grounded_field(
            metric="capital_expenditures",
            period="FY2026",
            extracted_value=26642,
            display_value="26,642",
            unit="USD millions",
            source_section="Cash Flows",
            source_snippet="Payments for property and equipment | (26,642) | (23,783)",
        )

        derived = safe_compute_derived(
            metric="free_cash_flow",
            formula="operating_cash_flow - capital_expenditures",
            unit="USD millions",
            inputs={"operating_cash_flow": ocf, "capital_expenditures": capex},
            compute_fn=lambda d: d["operating_cash_flow"] - d["capital_expenditures"],
        )

        self.assertFalse(derived.blocked)
        self.assertEqual(derived.value, 14923.0)


if __name__ == "__main__":
    unittest.main()

