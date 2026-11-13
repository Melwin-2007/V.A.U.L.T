"""Year-on-Year (YoY) Financial Validation Model Package."""

from __future__ import annotations

from YOY_model.demo import SAMPLE_COMPANIES, run_demo
from YOY_model.explain import explain
from YOY_model.integration import (
    apply_yoy_to_confidence,
    to_escalation,
    yoy_validation_node,
)
from YOY_model.multi_period import check_yoy_multi
from YOY_model.yoy import (
    DEFAULT_THRESHOLDS,
    DEFAULT_THRESHOLDS_PATH,
    analyze_company,
    check_yoy,
    load_thresholds,
)

__all__ = [
    "DEFAULT_THRESHOLDS",
    "DEFAULT_THRESHOLDS_PATH",
    "SAMPLE_COMPANIES",
    "analyze_company",
    "apply_yoy_to_confidence",
    "check_yoy",
    "check_yoy_multi",
    "explain",
    "load_thresholds",
    "run_demo",
    "to_escalation",
    "yoy_validation_node",
]

