"""Backwards-compatibility shim forwarding to YOY_model.multi_period."""

from __future__ import annotations

from YOY_model.multi_period import (
    _compute_modified_zscore,
    _normalize_series,
    check_yoy_multi,
)

__all__ = ["check_yoy_multi"]
