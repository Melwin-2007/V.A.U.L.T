"""V.A.U.L.T Deterministic Financial Data Pipeline Package.

Conforms to docs/CONTRACT.md section 5.
"""

from __future__ import annotations

from pipeline.accuracy import build_accuracy_report
from pipeline.edgar_client import fetch_filing
from pipeline.error_injection import apply_error_injection
from pipeline.extractor import extract_metrics
from pipeline.ground_truth import fetch_ground_truth
from pipeline.scoring import score_metrics
from pipeline.validators import run_validations

__all__ = [
    "fetch_filing",
    "extract_metrics",
    "score_metrics",
    "run_validations",
    "fetch_ground_truth",
    "build_accuracy_report",
    "apply_error_injection",
]
