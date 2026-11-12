from __future__ import annotations

from pathlib import Path
from typing import Iterable

from segmentation.confidence import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_YOY_THRESHOLD,
    escalate_metric_for_accounting_rule,
    validate_metric,
)
from segmentation.extractor import (
    ExtractedFilingData,
    RawMetricFact,
    RawSegmentFact,
    extract_from_cik,
    extract_from_xbrl_dir,
    find_matching_snippet_and_unit,
)
from segmentation.models import (
    AccountingEquationCheck,
    MetricValidation,
    ReviewDecision,
    SegmentLineItem,
    SegmentReconciliation,
    SegmentationReport,
)


def _validate_raw_metric(
    raw: RawMetricFact,
    data: ExtractedFilingData,
    *,
    confidence_threshold: float,
    yoy_threshold: float,
) -> MetricValidation:
    snippet, section, unit_ctx = find_matching_snippet_and_unit(
        data.tables,
        label_keywords=raw.label_keywords,
        raw_value=raw.current_value,
        fallback_scale=raw.fallback_scale,
        expected_year=raw.expected_year,
        default_section=raw.source_section_hint,
    )
    return validate_metric(
        metric=raw.metric,
        label=raw.label,
        raw_value=raw.current_value,
        prior_value=raw.prior_value,
        period=raw.period_label,
        source_section=section,
        source_snippet=snippet,
        unit_ctx=unit_ctx,
        s_label=raw.s_label,
        is_derived=raw.is_derived,
        confidence_threshold=confidence_threshold,
        yoy_threshold=yoy_threshold,
    )


def evaluate_extracted_data(
    data: ExtractedFilingData,
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    yoy_threshold: float = DEFAULT_YOY_THRESHOLD,
    tolerance_dollars: float | None = None,
    simulate_anomaly: str | None = None,
) -> SegmentationReport:
    """Run confidence scoring, segment reconciliation, accounting equation check, and escalation."""
    if simulate_anomaly:
        kind = simulate_anomaly.lower().replace("-", "_")
        if kind == "segment_math" and data.segment_items:
            # Mirror Apple 10-K prompt example: alter last segment value so sum != consolidated revenue
            orig = data.segment_items[-1]
            altered_val = round(orig.current_value * 0.72)
            data.segment_items[-1] = RawSegmentFact(
                name=f"{orig.name} (Misread Anomaly)",
                category=orig.category,
                axis=orig.axis,
                member=orig.member,
                concept=orig.concept,
                current_value=altered_val,
                prior_value=orig.prior_value,
                raw_xbrl_value=altered_val,
                period_label=orig.period_label,
                expected_year=orig.expected_year,
                fallback_scale=orig.fallback_scale,
                s_label=orig.s_label,
            )
        elif kind == "accounting_equation" and data.stockholders_equity:
            # Mirror Example C: inject typo into equity so Liabilities + Equity != Assets
            orig_eq = data.stockholders_equity
            altered_val = orig_eq.current_value + (10_000.0 * orig_eq.fallback_scale)
            data.stockholders_equity = RawMetricFact(
                metric=orig_eq.metric,
                label=f"{orig_eq.label} (Typo Anomaly)",
                concept=orig_eq.concept,
                current_value=altered_val,
                prior_value=orig_eq.prior_value,
                period_label=orig_eq.period_label,
                expected_year=orig_eq.expected_year,
                fallback_scale=orig_eq.fallback_scale,
                is_derived=orig_eq.is_derived,
                s_label=orig_eq.s_label,
                source_section_hint=orig_eq.source_section_hint,
                label_keywords=orig_eq.label_keywords,
            )
        elif kind == "hallucination":
            # Alter consolidated revenue to a hallucinated number absent from snippet
            orig_rev = data.consolidated_revenue
            altered_val = orig_rev.current_value + (99_999.0 * orig_rev.fallback_scale)
            data.consolidated_revenue = RawMetricFact(
                metric=orig_rev.metric,
                label=f"{orig_rev.label} (Hallucinated)",
                concept=orig_rev.concept,
                current_value=altered_val,
                prior_value=orig_rev.prior_value,
                period_label=orig_rev.period_label,
                expected_year=orig_rev.expected_year,
                fallback_scale=orig_rev.fallback_scale,
                is_derived=orig_rev.is_derived,
                s_label=orig_rev.s_label,
                source_section_hint=orig_rev.source_section_hint,
                label_keywords=["hallucinated"],
            )

    consolidated_rev = _validate_raw_metric(
        data.consolidated_revenue,
        data,
        confidence_threshold=confidence_threshold,
        yoy_threshold=yoy_threshold,
    )

    scale = data.consolidated_revenue.fallback_scale
    unit_suffix = "B" if scale == 1e9 else "K" if scale == 1e3 else "M" if scale == 1e6 else ""
    # Allow small rounding tolerance (e.g. <= 2 units of reported table scale due to rounding).
    tol = tolerance_dollars if tolerance_dollars is not None else (2.0 * scale)

    # 1. Validate individual segment & reconciling items
    segment_line_items: list[SegmentLineItem] = []
    for seg in data.segment_items:
        seg_keywords = [seg.name]
        if seg.category == "corporate_other" and "corporate" in seg.name.lower() and seg.name.lower() != "corporate":
            seg_keywords = ["corporate"]
        elif seg.category == "intersegment_elimination" and "elimination" in seg.name.lower():
            seg_keywords = ["elimination"]
        snippet, section, unit_ctx = find_matching_snippet_and_unit(
            data.tables,
            label_keywords=seg_keywords,
            raw_value=seg.raw_xbrl_value,
            fallback_scale=seg.fallback_scale,
            expected_year=seg.expected_year,
            default_section="Note - Segment Information",
        )
        m_val = validate_metric(
            metric=f"segment:{seg.name}",
            label=seg.name,
            raw_value=seg.raw_xbrl_value,
            prior_value=seg.prior_value,
            period=seg.period_label,
            source_section=section,
            source_snippet=snippet,
            unit_ctx=unit_ctx,
            s_label=seg.s_label,
            is_derived=False,
            confidence_threshold=confidence_threshold,
            yoy_threshold=yoy_threshold,
        )
        segment_line_items.append(
            SegmentLineItem(
                name=seg.name,
                category=seg.category,  # type: ignore[arg-type]
                axis=seg.axis,
                member=seg.member,
                value=seg.current_value,
                raw_xbrl_value=seg.raw_xbrl_value,
                validation=m_val,
            )
        )

    if data.is_single_segment:
        seg_recon = SegmentReconciliation(
            status="N/A - Single Segment",
            axis_used=None,
            breakdown_type=data.breakdown_type,
            unit=consolidated_rev.unit,
            segments_sum=0.0,
            corporate_other=0.0,
            intersegment_eliminations=0.0,
            reconciled_total=consolidated_rev.value,
            consolidated_total=consolidated_rev.value,
            difference=0.0,
            tolerance=tol,
            passed=True,
            reason=data.single_segment_reason or "Single operating segment; reconciliation skipped.",
            items=[],
        )
    else:
        seg_sum = sum(it.value for it in segment_line_items if it.category == "segment")
        corp_other = sum(it.value for it in segment_line_items if it.category == "corporate_other")
        elims = sum(it.value for it in segment_line_items if it.category == "intersegment_elimination")
        reconciled = seg_sum + corp_other - elims
        diff = reconciled - consolidated_rev.value
        recon_passed = abs(diff) <= tol
        seg_status = "PASS" if recon_passed else "FAIL"
        reason = (
            f"Exact reconciliation within tolerance: {reconciled / scale:,.2f}{unit_suffix} vs "
            f"Consolidated {consolidated_rev.value / scale:,.2f}{unit_suffix} (diff={diff / scale:,.2f}{unit_suffix})."
            if recon_passed
            else (
                f"Segment Reconciliation Failed: sum(segments) + corporate - eliminations = "
                f"{reconciled / scale:,.2f}{unit_suffix} != Consolidated {consolidated_rev.value / scale:,.2f}{unit_suffix} "
                f"(diff={diff / scale:,.2f}{unit_suffix})."
            )
        )
        if not recon_passed:
            # Trigger B: even if individual score is 1.00, force Human Review on all segment metrics and consolidated revenue.
            segment_line_items = [
                SegmentLineItem(
                    name=it.name,
                    category=it.category,
                    axis=it.axis,
                    member=it.member,
                    value=it.value,
                    raw_xbrl_value=it.raw_xbrl_value,
                    validation=escalate_metric_for_accounting_rule(it.validation, reason),
                )
                for it in segment_line_items
            ]
            consolidated_rev = escalate_metric_for_accounting_rule(consolidated_rev, reason)

        seg_recon = SegmentReconciliation(
            status=seg_status,
            axis_used=data.axis_used,
            breakdown_type=data.breakdown_type,
            unit=consolidated_rev.unit,
            segments_sum=seg_sum,
            corporate_other=corp_other,
            intersegment_eliminations=elims,
            reconciled_total=reconciled,
            consolidated_total=consolidated_rev.value,
            difference=diff,
            tolerance=tol,
            passed=recon_passed,
            reason=reason,
            items=segment_line_items,
        )

    # 2. Trigger B Hard Accounting Rule: Balance Sheet Accounting Equation (Assets == Liabilities + Equity)
    bs_metrics: dict[str, MetricValidation] = {}
    for key, raw_fact in (
        ("total_assets", data.total_assets),
        ("total_liabilities", data.total_liabilities),
        ("stockholders_equity", data.stockholders_equity),
    ):
        if raw_fact is not None:
            bs_metrics[key] = _validate_raw_metric(
                raw_fact,
                data,
                confidence_threshold=confidence_threshold,
                yoy_threshold=yoy_threshold,
            )

    a_val = bs_metrics["total_assets"].value if "total_assets" in bs_metrics else None
    l_val = bs_metrics["total_liabilities"].value if "total_liabilities" in bs_metrics else None
    e_val = bs_metrics["stockholders_equity"].value if "stockholders_equity" in bs_metrics else None

    if a_val is not None and l_val is not None and e_val is not None:
        l_plus_e = l_val + e_val
        bs_diff = a_val - l_plus_e
        bs_passed = abs(bs_diff) <= tol
        bs_reason = (
            f"Balance Sheet reconciles: Assets ({a_val / scale:,.2f}{unit_suffix}) == Liabilities + Equity ({l_plus_e / scale:,.2f}{unit_suffix})."
            if bs_passed
            else (
                f"Accounting Equation Failed: Total Assets ({a_val / scale:,.2f}{unit_suffix}) != "
                f"Liabilities + Equity ({l_plus_e / scale:,.2f}{unit_suffix}), diff={bs_diff / scale:,.2f}{unit_suffix}."
            )
        )
        if not bs_passed:
            # Trigger B: escalate ALL 3 balance sheet metrics even if confidence >= 0.85.
            bs_metrics = {
                k: escalate_metric_for_accounting_rule(v, bs_reason)
                for k, v in bs_metrics.items()
            }
    else:
        l_plus_e = None
        bs_diff = None
        bs_passed = False
        bs_reason = "Incomplete Balance Sheet facts; unable to verify Assets == Liabilities + Equity."

    acct_check = AccountingEquationCheck(
        total_assets=a_val,
        total_liabilities=l_val,
        stockholders_equity=e_val,
        liabilities_plus_equity=l_plus_e,
        difference=bs_diff,
        tolerance=tol,
        unit=consolidated_rev.unit,
        passed=bs_passed,
        reason=bs_reason,
        metrics=bs_metrics,
    )

    # 3. Aggregate Escalation Reasons (Trigger A + Trigger B)
    all_metrics: list[MetricValidation] = [
        consolidated_rev,
        *[it.validation for it in seg_recon.items],
        *acct_check.metrics.values(),
    ]
    escalation_reasons: list[str] = []
    if not seg_recon.passed:
        escalation_reasons.append(seg_recon.reason)
    if not acct_check.passed:
        escalation_reasons.append(acct_check.reason)
    for mv in all_metrics:
        if mv.decision == "HUMAN_REVIEW":
            for r in mv.escalation_reasons:
                if (not seg_recon.passed and r == seg_recon.reason) or (not acct_check.passed and r == acct_check.reason):
                    continue
                msg = f"[{mv.metric}] {r}"
                if msg not in escalation_reasons:
                    escalation_reasons.append(msg)

    review_decision: ReviewDecision = "HUMAN_REVIEW" if escalation_reasons else "AUTO-APPROVED"
    verdict = "PASS" if review_decision == "AUTO-APPROVED" else "FAIL"

    return SegmentationReport(
        cik=data.cik,
        company_name=data.company_name,
        form=data.form,
        accession_no=data.accession_no,
        filing_date=data.filing_date,
        period_of_report=data.period_of_report,
        sec_url=data.sec_url,
        confidence_threshold=confidence_threshold,
        verdict=verdict,
        review_decision=review_decision,
        escalation_reasons=escalation_reasons,
        consolidated_revenue=consolidated_rev,
        segment_reconciliation=seg_recon,
        accounting_equation=acct_check,
    )


def validate_company_segments(
    cik: str,
    *,
    form: str = "10-K",
    identity: str | None = None,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    yoy_threshold: float = DEFAULT_YOY_THRESHOLD,
    tolerance_dollars: float | None = None,
    simulate_anomaly: str | None = None,
) -> SegmentationReport:
    """Fetch a company's latest 10-K by CIK and run full segment & confidence validation."""
    data = extract_from_cik(cik, form=form, identity=identity)
    return evaluate_extracted_data(
        data,
        confidence_threshold=confidence_threshold,
        yoy_threshold=yoy_threshold,
        tolerance_dollars=tolerance_dollars,
        simulate_anomaly=simulate_anomaly,
    )


def validate_xbrl_directory(
    xbrl_dir: str | Path,
    *,
    cik: str = "0000000000",
    company_name: str = "Offline XBRL Company",
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    yoy_threshold: float = DEFAULT_YOY_THRESHOLD,
    tolerance_dollars: float | None = None,
    simulate_anomaly: str | None = None,
) -> SegmentationReport:
    """Run full segment & confidence validation on a local XBRL directory fixture."""
    data = extract_from_xbrl_dir(xbrl_dir, cik=cik, company_name=company_name)
    return evaluate_extracted_data(
        data,
        confidence_threshold=confidence_threshold,
        yoy_threshold=yoy_threshold,
        tolerance_dollars=tolerance_dollars,
        simulate_anomaly=simulate_anomaly,
    )
