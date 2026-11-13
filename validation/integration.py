"""Backwards-compatibility shim forwarding to YOY_model.integration."""

from __future__ import annotations

from YOY_model.integration import (
    _deterministic_key,
    apply_yoy_to_confidence,
    to_escalation,
    yoy_validation_node,
)

__all__ = [
    "apply_yoy_to_confidence",
    "to_escalation",
    "yoy_validation_node",
]
