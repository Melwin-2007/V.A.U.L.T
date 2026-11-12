from __future__ import annotations

import os
from pathlib import Path

_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("EDGAR_LOCAL_DATA_DIR", str(_WORKSPACE_ROOT / ".edgar"))
os.environ.setdefault("EDGAR_CACHE_DIR", str(_WORKSPACE_ROOT / ".edgar_cache"))

from segmentation.models import (
    AccountingEquationCheck,
    ConfidenceBreakdown,
    MetricValidation,
    SegmentLineItem,
    SegmentReconciliation,
    SegmentationReport,
    UnitContext,
)
from segmentation.validator import validate_company_segments, validate_xbrl_directory

__all__ = [
    "AccountingEquationCheck",
    "ConfidenceBreakdown",
    "MetricValidation",
    "SegmentLineItem",
    "SegmentReconciliation",
    "SegmentationReport",
    "UnitContext",
    "validate_company_segments",
    "validate_xbrl_directory",
]

