"""Backwards-compatibility shim forwarding to YOY_model.yoy."""

from __future__ import annotations

from YOY_model.yoy import (
    DEFAULT_THRESHOLDS,
    DEFAULT_THRESHOLDS_PATH,
    _YEAR_EXTRACT_RE,
    _extract_value_and_meta,
    _extract_year,
    _not_applicable,
    _resolve_threshold,
    _to_finite_float,
    analyze_company,
    check_yoy,
    load_thresholds,
)

__all__ = [
    "DEFAULT_THRESHOLDS",
    "DEFAULT_THRESHOLDS_PATH",
    "analyze_company",
    "check_yoy",
    "load_thresholds",
]
