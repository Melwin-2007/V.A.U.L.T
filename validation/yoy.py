"""Core Year-on-Year (YoY) financial validation checks."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_THRESHOLDS_PATH = Path(__file__).resolve().parent.parent / "config" / "thresholds.yaml"
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "default_pct": 40.0,
    "warn_ratio": 0.75,
    "confidence_multipliers": {"fail": 0.60, "warn": 0.85},
    "metrics": {
        "revenue": 50.0,
        "net_income": 150.0,
        "total_assets": 30.0,
        "total_liabilities": 40.0,
        "shareholders_equity": 40.0,
        "operating_cash_flow": 60.0,
    },
}

_YEAR_EXTRACT_RE = re.compile(r"((?:19|20)\d{2})")


def load_thresholds(path: Optional[str | Path] = None) -> dict[str, Any]:
    """Load configurable YoY thresholds from a YAML file with safe defaults."""
    target = Path(path) if path is not None else DEFAULT_THRESHOLDS_PATH
    try:
        if not target.exists():
            return dict(DEFAULT_THRESHOLDS)
        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULT_THRESHOLDS)
        merged = dict(DEFAULT_THRESHOLDS)
        merged.update(data)
        if isinstance(data.get("metrics"), dict):
            merged["metrics"] = {
                str(k).strip().lower(): float(v) for k, v in data["metrics"].items()
            }
        return merged
    except Exception:
        return dict(DEFAULT_THRESHOLDS)


def _to_finite_float(value: Any) -> Optional[float]:
    """Safely convert numbers or numeric strings to finite float, else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        fval = float(value)
        return fval if math.isfinite(fval) else None
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        try:
            fval = float(cleaned)
        except ValueError:
            return None
        return fval if math.isfinite(fval) else None
    try:
        fval = float(value)
        return fval if math.isfinite(fval) else None
    except (TypeError, ValueError):
        return None


def _extract_value_and_meta(raw: Any) -> tuple[Any, Optional[str], Any]:
    """Accept either a scalar value or a dict containing value/unit/period."""
    if isinstance(raw, dict):
        return raw.get("value"), raw.get("unit"), raw.get("period")
    return raw, None, None


def _resolve_threshold(metric: str, thresholds: Optional[dict[str, Any]]) -> tuple[float, float]:
    """Return (fail_threshold_pct, warn_ratio) for a given metric."""
    cfg = thresholds if isinstance(thresholds, dict) else DEFAULT_THRESHOLDS
    metric_key = metric.strip().lower()

    default_pct = _to_finite_float(cfg.get("default_pct", cfg.get("default")))
    if default_pct is None or default_pct < 0:
        default_pct = float(DEFAULT_THRESHOLDS["default_pct"])

    warn_ratio = _to_finite_float(cfg.get("warn_ratio"))
    if warn_ratio is None or not (0.0 < warn_ratio <= 1.0):
        warn_ratio = float(DEFAULT_THRESHOLDS["warn_ratio"])

    metrics_map = cfg.get("metrics")
    if isinstance(metrics_map, dict):
        normalized_map = {str(k).strip().lower(): v for k, v in metrics_map.items()}
        override = _to_finite_float(normalized_map.get(metric_key))
        if override is not None and override >= 0:
            return override, warn_ratio

    # Also support flat override dicts like {"revenue": 50, "default": 40}
    flat_normalized = {str(k).strip().lower(): v for k, v in cfg.items()}
    if metric_key in flat_normalized and metric_key not in {
        "default_pct",
        "default",
        "warn_ratio",
        "metrics",
        "confidence_multipliers",
    }:
        override = _to_finite_float(flat_normalized[metric_key])
        if override is not None and override >= 0:
            return override, warn_ratio

    return default_pct, warn_ratio


def _extract_year(period: Any) -> Optional[int]:
    if period is None or isinstance(period, bool):
        return None
    if isinstance(period, int):
        return period
    match = _YEAR_EXTRACT_RE.search(str(period))
    return int(match.group(1)) if match else None


def _not_applicable(
    metric: str,
    reason: str,
    threshold_used: Optional[float] = None,
    *,
    current_value: Optional[float] = None,
    prior_value: Optional[float] = None,
    unit: Optional[str] = None,
    current_period: Any = None,
    prior_period: Any = None,
    source_snippet: Optional[str] = None,
    company: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "check": "yoy_change",
        "metric": metric,
        "status": "not_applicable",
        "passed": False,
        "pct_change": None,
        "abs_change": None,
        "threshold_used": threshold_used,
        "severity": "not_applicable",
        "anomaly_type": "not_applicable",
        "reason": reason,
        "suggested_action": "Verify missing, mismatched, or zero-base data in the source filing.",
        "current_value": current_value,
        "prior_value": prior_value,
        "unit": unit,
        "current_period": current_period,
        "prior_period": prior_period,
        "source_snippet": source_snippet,
        "company": company,
    }


def check_yoy(
    metric: str,
    current: Any,
    prior: Any,
    thresholds: Optional[dict[str, Any]] = None,
    *,
    current_unit: Optional[str] = None,
    prior_unit: Optional[str] = None,
    current_period: Any = None,
    prior_period: Any = None,
    source_snippet: Optional[str] = None,
    company: Optional[str] = None,
) -> dict[str, Any]:
    """Validate Year-on-Year change for a single financial metric.

    Pure, deterministic, and non-raising: invalid or missing inputs yield a
    structured ``not_applicable`` result rather than raising or faking a pass.
    """
    metric_name = str(metric or "unknown_metric").strip() or "unknown_metric"
    threshold_used, warn_ratio = _resolve_threshold(metric_name, thresholds)

    raw_curr, meta_curr_unit, meta_curr_period = _extract_value_and_meta(current)
    raw_prev, meta_prev_unit, meta_prev_period = _extract_value_and_meta(prior)

    c_unit = current_unit if current_unit is not None else meta_curr_unit
    p_unit = prior_unit if prior_unit is not None else meta_prev_unit
    c_period = current_period if current_period is not None else meta_curr_period
    p_period = prior_period if prior_period is not None else meta_prev_period

    curr_val = _to_finite_float(raw_curr)
    prior_val = _to_finite_float(raw_prev)

    if c_unit and p_unit:
        if c_unit.strip().lower() != p_unit.strip().lower():
            return _not_applicable(
                metric_name,
                f"Unit mismatch for {metric_name}: current unit ({c_unit!r}) != prior unit ({p_unit!r}).",
                threshold_used,
                current_value=curr_val,
                prior_value=prior_val,
                unit=c_unit,
                current_period=c_period,
                prior_period=p_period,
                source_snippet=source_snippet,
                company=company,
            )

    resolved_unit = c_unit or p_unit

    if c_period is not None and p_period is not None:
        if str(c_period).strip().lower() == str(p_period).strip().lower():
            return _not_applicable(
                metric_name,
                f"Period mismatch for {metric_name}: current period ({c_period!r}) equals prior period ({p_period!r}).",
                threshold_used,
                current_value=curr_val,
                prior_value=prior_val,
                unit=resolved_unit,
                current_period=c_period,
                prior_period=p_period,
                source_snippet=source_snippet,
                company=company,
            )
        curr_yr = _extract_year(c_period)
        prev_yr = _extract_year(p_period)
        if curr_yr is not None and prev_yr is not None and curr_yr <= prev_yr:
            return _not_applicable(
                metric_name,
                f"Invalid period ordering for {metric_name}: current period ({c_period}) is not later than prior period ({p_period}).",
                threshold_used,
                current_value=curr_val,
                prior_value=prior_val,
                unit=resolved_unit,
                current_period=c_period,
                prior_period=p_period,
                source_snippet=source_snippet,
                company=company,
            )

    if curr_val is None or prior_val is None:
        return _not_applicable(
            metric_name,
            f"Missing or non-numeric value for {metric_name} (current={raw_curr!r}, prior={raw_prev!r}).",
            threshold_used,
            current_value=curr_val,
            prior_value=prior_val,
            unit=resolved_unit,
            current_period=c_period,
            prior_period=p_period,
            source_snippet=source_snippet,
            company=company,
        )

    if prior_val == 0.0:
        return _not_applicable(
            metric_name,
            f"Prior value for {metric_name} is zero; YoY percentage change is undefined.",
            threshold_used,
            current_value=curr_val,
            prior_value=prior_val,
            unit=resolved_unit,
            current_period=c_period,
            prior_period=p_period,
            source_snippet=source_snippet,
            company=company,
        )

    abs_change = round(curr_val - prior_val, 6)
    # Use abs(prior_val) in the denominator for negative priors per specification.
    pct_change = round(((curr_val - prior_val) / abs(prior_val)) * 100.0, 4)
    abs_pct = abs(pct_change)

    is_sign_flip = (prior_val > 0 and curr_val < 0) or (prior_val < 0 and curr_val > 0)
    ratio = abs(curr_val / prior_val) if curr_val != 0 else 0.0
    is_likely_unit_error = (500.0 <= ratio <= 2000.0) or (0.0005 <= ratio <= 0.002)

    warn_threshold = threshold_used * warn_ratio

    if is_sign_flip:
        passed = False
        severity = "fail"
        status = "fail"
        anomaly_type = "sign_flip"
        direction = "profit to loss" if prior_val > 0 else "loss to profit"
        reason = (
            f"{metric_name} flipped from {direction} "
            f"({prior_val} -> {curr_val}, {pct_change:+.2f}% YoY). "
            "Sign reversals are always escalated for human confirmation."
        )
        suggested_action = (
            "Verify sign and classification in the source 10-K statement; "
            "confirm whether this reflects a real operational reversal or sign extraction error."
        )
    elif abs_pct > threshold_used:
        passed = False
        severity = "fail"
        status = "fail"
        if is_likely_unit_error:
            anomaly_type = "likely_unit_error"
            scale_desc = f"~{ratio:.0f}x" if ratio >= 1 else f"~{ratio:.4f}x"
            reason = (
                f"{metric_name} changed by {pct_change:+.2f}% ({scale_desc}, "
                f"{prior_val} -> {curr_val}), far exceeding the {threshold_used:.1f}% threshold. "
                "Magnitude strongly suggests a unit/scale extraction error (e.g. 1000x mismatch)."
            )
            suggested_action = (
                "Check table unit headers (thousands vs. millions vs. exact dollars) "
                "and correct the extracted scale if misread."
            )
        else:
            anomaly_type = "large_change"
            reason = (
                f"{metric_name} changed by {pct_change:+.2f}% ({prior_val} -> {curr_val}), "
                f"exceeding the {threshold_used:.1f}% review threshold. "
                "Could reflect a genuine business event (M&A, one-off) or extraction error."
            )
            suggested_action = (
                "Confirm against the source 10-K snippet and MD&A narrative whether "
                "this is a genuine financial shift."
            )
    elif abs_pct >= warn_threshold:
        passed = True
        severity = "warn"
        status = "warn"
        anomaly_type = "approaching_threshold"
        reason = (
            f"{metric_name} changed by {pct_change:+.2f}% ({prior_val} -> {curr_val}), "
            f"approaching the {threshold_used:.1f}% threshold."
        )
        suggested_action = None
    else:
        passed = True
        severity = "ok"
        status = "pass"
        anomaly_type = "none"
        reason = (
            f"{metric_name} changed by {pct_change:+.2f}% ({prior_val} -> {curr_val}), "
            f"within the {threshold_used:.1f}% threshold."
        )
        suggested_action = None

    return {
        "check": "yoy_change",
        "metric": metric_name,
        "status": status,
        "passed": passed,
        "pct_change": pct_change,
        "abs_change": abs_change,
        "threshold_used": threshold_used,
        "severity": severity,
        "anomaly_type": anomaly_type,
        "reason": reason,
        "suggested_action": suggested_action,
        "current_value": curr_val,
        "prior_value": prior_val,
        "unit": resolved_unit,
        "current_period": c_period,
        "prior_period": p_period,
        "source_snippet": source_snippet,
        "company": company,
    }


def analyze_company(
    metrics: dict[str, Any] | list[dict[str, Any]],
    thresholds: Optional[dict[str, Any]] = None,
    *,
    company: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Run YoY validation checks across all extracted metrics for one company."""
    cfg = thresholds if thresholds is not None else load_thresholds()
    results: list[dict[str, Any]] = []

    if isinstance(metrics, dict):
        items = [
            {"name": key, **(val if isinstance(val, dict) else {"current_value": val})}
            for key, val in metrics.items()
        ]
    elif isinstance(metrics, list):
        items = list(metrics)
    else:
        return [
            _not_applicable(
                "unknown_metric",
                f"Invalid metrics container type: {type(metrics).__name__}.",
                company=company,
            )
        ]

    for item in items:
        if not isinstance(item, dict):
            results.append(
                _not_applicable(
                    "unknown_metric",
                    f"Invalid metric entry type: {type(item).__name__}.",
                    company=company,
                )
            )
            continue

        metric_name = str(item.get("name") or item.get("metric") or "unknown_metric")
        unit = item.get("unit")
        curr_unit = item.get("current_unit", unit)
        prev_unit = item.get("prior_unit", unit)

        result = check_yoy(
            metric=metric_name,
            current=item.get("current_value"),
            prior=item.get("prior_value"),
            thresholds=cfg,
            current_unit=curr_unit,
            prior_unit=prev_unit,
            current_period=item.get("current_period"),
            prior_period=item.get("prior_period"),
            source_snippet=item.get("source_snippet"),
            company=item.get("company") or company,
        )
        results.append(result)

    return results
