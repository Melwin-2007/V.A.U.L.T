"""Deterministic Accounting Equation Validation package."""

from __future__ import annotations

from typing import Any

from .adapter import list_available_filings, load_balance_sheet
from .models import (
    BalanceSheetInput,
    BalanceSheetPeriodInput,
    CheckResult,
    MetricDecision,
    ValidationConfig,
    ValidationReport,
    load_config,
)

__all__ = [
    "BalanceSheetInput",
    "BalanceSheetPeriodInput",
    "CheckResult",
    "MetricDecision",
    "ValidationConfig",
    "ValidationReport",
    "list_available_filings",
    "load_balance_sheet",
    "load_config",
    "validate_accounting_equation",
    "validate_balance_sheet_input",
]


def __getattr__(name: str) -> Any:
    if name in ("validate_accounting_equation", "validate_balance_sheet_input"):
        from . import accounting_equation

        return getattr(accounting_equation, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
