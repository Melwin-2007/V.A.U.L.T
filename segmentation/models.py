from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ReviewDecision = Literal["AUTO-APPROVED", "HUMAN_REVIEW"]
OverallVerdict = Literal["PASS", "FAIL"]
SegmentStatus = Literal["PASS", "FAIL", "N/A - Single Segment"]


@dataclass(frozen=True)
class UnitContext:
    """Detected table unit scale and fiscal period metadata."""

    unit_label: str
    scale_divisor: float
    fiscal_period_detected: bool
    unit_scale_detected: bool
    fiscal_label: str
    header_snippet: str


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """Weighted confidence score breakdown on a [0.00, 1.00] scale."""

    s_grounding: float
    s_label: float
    s_unit: float
    s_yoy: float
    score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetricValidation:
    """Validation, grounding, and escalation verdict for a single financial metric."""

    metric: str
    label: str
    value: float
    prior_value: float | None
    display_value: str
    unit: str
    period: str
    yoy_change_pct: float | None
    source_section: str
    source_snippet: str
    grounding_passed: bool
    hallucination_flag: bool
    matched_text: str | None
    confidence: ConfidenceBreakdown
    decision: ReviewDecision
    escalation_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SegmentLineItem:
    """Extracted segment or reconciling line item with validation metadata."""

    name: str
    category: Literal["segment", "corporate_other", "intersegment_elimination"]
    axis: str
    member: str
    value: float
    raw_xbrl_value: float
    validation: MetricValidation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SegmentReconciliation:
    """ASC 280 segment-to-consolidated revenue reconciliation check."""

    status: SegmentStatus
    axis_used: str | None
    breakdown_type: str
    unit: str
    segments_sum: float
    corporate_other: float
    intersegment_eliminations: float
    reconciled_total: float
    consolidated_total: float
    difference: float
    tolerance: float
    passed: bool
    reason: str
    items: list[SegmentLineItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccountingEquationCheck:
    """Balance Sheet hard accounting rule: Total Assets == Total Liabilities + Equity."""

    total_assets: float | None
    total_liabilities: float | None
    stockholders_equity: float | None
    liabilities_plus_equity: float | None
    difference: float | None
    tolerance: float
    unit: str
    passed: bool
    reason: str
    metrics: dict[str, MetricValidation] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SegmentationReport:
    """Complete CIK segment & accounting validation report."""

    cik: str
    company_name: str
    form: str
    accession_no: str
    filing_date: str
    period_of_report: str
    sec_url: str
    confidence_threshold: float
    verdict: OverallVerdict
    review_decision: ReviewDecision
    escalation_reasons: list[str]
    consolidated_revenue: MetricValidation
    segment_reconciliation: SegmentReconciliation
    accounting_equation: AccountingEquationCheck

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

