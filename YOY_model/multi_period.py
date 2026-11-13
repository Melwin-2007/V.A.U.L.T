"""Multi-period (3+ reporting periods) Year-on-Year anomaly detection."""

from __future__ import annotations

import math
import statistics
from typing import Any, Optional

from YOY_model.yoy import _to_finite_float, check_yoy


def _normalize_series(periods: list[Any]) -> list[tuple[Any, float, Optional[str]]]:
    """Normalize period inputs into chronological (period_label, value, unit) tuples."""
    parsed: list[tuple[Any, float, Optional[str]]] = []
    for idx, entry in enumerate(periods):
        if isinstance(entry, dict):
            val = _to_finite_float(entry.get("value"))
            if val is None:
                return []
            parsed.append((entry.get("period", idx), val, entry.get("unit")))
        else:
            val = _to_finite_float(entry)
            if val is None:
                return []
            parsed.append((idx, val, None))

    # Sort chronologically if all period labels are comparable integers or year strings
    if all(isinstance(p[0], int) for p in parsed):
        parsed.sort(key=lambda item: item[0])
    return parsed


def _compute_modified_zscore(latest: float, history: list[float]) -> Optional[float]:
    """Compute modified z-score using Median Absolute Deviation (MAD)."""
    if len(history) < 2:
        return None
    med = statistics.median(history)
    abs_devs = [abs(x - med) for x in history]
    mad = statistics.median(abs_devs)
    if mad == 0.0:
        return None
    return 0.6745 * (latest - med) / mad


def check_yoy_multi(
    metric: str,
    periods: list[Any],
    *,
    z_threshold: float = 2.0,
    thresholds: Optional[dict[str, Any]] = None,
    source_snippet: Optional[str] = None,
    company: Optional[str] = None,
) -> dict[str, Any]:
    """Evaluate a 3+ period metric series using YoY history and z-score / MAD.

    Returns the standard YoY check schema extended with ``method: "zscore"``,
    ``z_score``, and ``yoy_series``.
    """
    metric_name = str(metric or "unknown_metric").strip() or "unknown_metric"
    if not isinstance(periods, list) or len(periods) < 3:
        return {
            "check": "yoy_change",
            "method": "zscore",
            "metric": metric_name,
            "status": "not_applicable",
            "passed": False,
            "pct_change": None,
            "abs_change": None,
            "threshold_used": z_threshold,
            "severity": "not_applicable",
            "anomaly_type": "not_applicable",
            "reason": "At least 3 reporting periods are required for multi-period YoY anomaly detection.",
            "suggested_action": "Provide 3 or more historical periods for multi-period statistical checks.",
            "z_score": None,
            "yoy_series": [],
            "company": company,
            "source_snippet": source_snippet,
        }

    series = _normalize_series(periods)
    if len(series) < 3:
        return {
            "check": "yoy_change",
            "method": "zscore",
            "metric": metric_name,
            "status": "not_applicable",
            "passed": False,
            "pct_change": None,
            "abs_change": None,
            "threshold_used": z_threshold,
            "severity": "not_applicable",
            "anomaly_type": "not_applicable",
            "reason": "Multi-period series contains missing or non-numeric values.",
            "suggested_action": "Verify numeric values across all reporting periods.",
            "z_score": None,
            "yoy_series": [],
            "company": company,
            "source_snippet": source_snippet,
        }

    units = {u.strip().lower() for _, _, u in series if u}
    if len(units) > 1:
        return {
            "check": "yoy_change",
            "method": "zscore",
            "metric": metric_name,
            "status": "not_applicable",
            "passed": False,
            "pct_change": None,
            "abs_change": None,
            "threshold_used": z_threshold,
            "severity": "not_applicable",
            "anomaly_type": "not_applicable",
            "reason": f"Incompatible units across multi-period history for {metric_name}: {sorted(units)}.",
            "suggested_action": "Normalize units across all historical periods before multi-period validation.",
            "z_score": None,
            "yoy_series": [],
            "company": company,
            "source_snippet": source_snippet,
        }

    yoy_series: list[float] = []
    for i in range(1, len(series)):
        prev_period, prev_val, prev_unit = series[i - 1]
        curr_period, curr_val, curr_unit = series[i]
        step = check_yoy(
            metric=metric_name,
            current=curr_val,
            prior=prev_val,
            thresholds=thresholds,
            current_unit=curr_unit,
            prior_unit=prev_unit,
            current_period=curr_period,
            prior_period=prev_period,
            source_snippet=source_snippet,
            company=company,
        )
        if step["pct_change"] is None:
            return {
                **step,
                "method": "zscore",
                "z_score": None,
                "yoy_series": yoy_series,
            }
        yoy_series.append(float(step["pct_change"]))

    latest_yoy = yoy_series[-1]
    history = yoy_series[:-1]
    latest_period, latest_val, latest_unit = series[-1]
    prior_period, prior_val, _ = series[-2]
    abs_change = round(latest_val - prior_val, 6)

    # Compute standard deviation z-score against prior YoY history (fall back to MAD if needed)
    z_score: Optional[float] = None
    if len(history) >= 2:
        mean_hist = statistics.mean(history)
        stdev_hist = statistics.stdev(history)
        if stdev_hist > 0.0:
            z_score = round((latest_yoy - mean_hist) / stdev_hist, 4)
        else:
            mod_z = _compute_modified_zscore(latest_yoy, history)
            if mod_z is not None:
                z_score = round(mod_z, 4)
            elif abs(latest_yoy - mean_hist) > 1e-9:
                # Zero historical variance and latest change diverged
                z_score = 999.0
            else:
                z_score = 0.0
    else:
        # Exactly 3 periods gives 1 prior YoY value and 1 latest YoY value
        baseline = history[0]
        scale = max(abs(baseline), 10.0)
        z_score = round((latest_yoy - baseline) / scale, 4)

    is_anomalous = z_score is not None and abs(z_score) > z_threshold
    ratio = abs(latest_val / prior_val) if prior_val != 0 and latest_val != 0 else 0.0
    is_unit_error = (500.0 <= ratio <= 2000.0) or (0.0005 <= ratio <= 0.002)

    if is_anomalous:
        passed = False
        severity = "fail"
        status = "fail"
        anomaly_type = "likely_unit_error" if is_unit_error else "historical_outlier"
        reason = (
            f"{metric_name} latest YoY change ({latest_yoy:+.2f}%) has |z-score|={abs(z_score):.2f} "
            f"(>{z_threshold:.1f} SD from historical YoY series {history})."
        )
        suggested_action = (
            "Verify unit/scale headers in the filing (possible 1000x scale mismatch)."
            if is_unit_error
            else "Review MD&A for structural business shifts explaining this break from historical trend."
        )
    else:
        passed = True
        severity = "ok"
        status = "pass"
        anomaly_type = "none"
        reason = (
            f"{metric_name} latest YoY change ({latest_yoy:+.2f}%) is consistent with "
            f"historical YoY trend (z-score={z_score if z_score is not None else 0.0:.2f})."
        )
        suggested_action = None

    return {
        "check": "yoy_change",
        "method": "zscore",
        "metric": metric_name,
        "status": status,
        "passed": passed,
        "pct_change": latest_yoy,
        "abs_change": abs_change,
        "threshold_used": z_threshold,
        "severity": severity,
        "anomaly_type": anomaly_type,
        "reason": reason,
        "suggested_action": suggested_action,
        "z_score": z_score if (z_score is None or math.isfinite(z_score)) else None,
        "yoy_series": yoy_series,
        "current_value": latest_val,
        "prior_value": prior_val,
        "unit": latest_unit,
        "current_period": latest_period,
        "prior_period": prior_period,
        "source_snippet": source_snippet,
        "company": company,
    }

