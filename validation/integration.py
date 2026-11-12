"""Integration helpers for confidence scoring, HITL escalation, and LangGraph."""

from __future__ import annotations

from typing import Any, Optional

from validation.yoy import analyze_company, load_thresholds


def apply_yoy_to_confidence(
    metric_result: dict[str, Any] | float | int,
    yoy_result: dict[str, Any],
    *,
    fail_multiplier: float = 0.6,
    warn_multiplier: float = 0.85,
) -> float:
    """Adjust extraction confidence based on YoY validation severity.

    - ``fail`` (and not ``not_applicable``): multiply by ``fail_multiplier`` (default 0.6)
    - ``warn``: multiply by ``warn_multiplier`` (default 0.85)
    - ``ok`` or ``not_applicable``: unchanged
    """
    if isinstance(metric_result, dict):
        raw_conf = metric_result.get("confidence", 1.0)
    else:
        raw_conf = metric_result

    try:
        base_conf = float(raw_conf)
    except (TypeError, ValueError):
        base_conf = 1.0
    base_conf = max(0.0, min(1.0, base_conf))

    if not isinstance(yoy_result, dict):
        return round(base_conf, 6)

    severity = yoy_result.get("severity")
    status = yoy_result.get("status")

    if status == "not_applicable" or severity == "not_applicable":
        adjusted = base_conf
    elif severity == "fail" or yoy_result.get("passed") is False:
        adjusted = base_conf * fail_multiplier
    elif severity == "warn":
        adjusted = base_conf * warn_multiplier
    else:
        adjusted = base_conf

    return round(max(0.0, min(1.0, adjusted)), 6)


def to_escalation(
    yoy_result: dict[str, Any],
    *,
    source_snippet: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Convert a failed YoY check into a human-in-the-loop escalation payload."""
    if not isinstance(yoy_result, dict):
        return None
    status = yoy_result.get("status")
    severity = yoy_result.get("severity")
    if status == "not_applicable" or severity == "not_applicable":
        return None
    if yoy_result.get("passed", True) and severity != "fail":
        return None

    snippet = (
        source_snippet
        if source_snippet is not None
        else yoy_result.get("source_snippet")
    )
    suggested_action = yoy_result.get("suggested_action") or (
        "Review source 10-K filing to confirm whether the change is genuine or an extraction error."
    )

    return {
        "metric": yoy_result.get("metric"),
        "reason": yoy_result.get("reason"),
        "severity": severity or "fail",
        "source_snippet": snippet,
        "suggested_action": suggested_action,
    }


def _deterministic_key(company: Any, metric: Any, period: Any) -> str:
    comp_s = str(company or "unknown").strip()
    met_s = str(metric or "unknown").strip()
    per_s = str(period or "unknown").strip()
    return f"{comp_s}:{met_s}:yoy:{per_s}"


def yoy_validation_node(state: dict[str, Any]) -> dict[str, Any]:
    """LangGraph-compatible, idempotent node for Year-on-Year validation.

    Reads ``state["metrics"]`` and returns updated ``validation_results`` and
    ``escalations`` lists without mutating input ``state`` or duplicating entries
    when executed multiple times on the same state.
    """
    metrics = state.get("metrics") or {}
    company = state.get("company")
    thresholds = state.get("thresholds") or load_thresholds()

    conf_cfg = thresholds.get("confidence_multipliers", {})
    fail_mult = float(conf_cfg.get("fail", 0.6))
    warn_mult = float(conf_cfg.get("warn", 0.85))

    existing_validations = list(state.get("validation_results") or [])
    existing_escalations = list(state.get("escalations") or [])

    seen_val_keys: set[str] = {
        str(v["key"])
        for v in existing_validations
        if isinstance(v, dict) and v.get("key")
    }
    seen_esc_keys: set[str] = {
        str(e["key"])
        for e in existing_escalations
        if isinstance(e, dict) and e.get("key")
    }

    # Map normalized metric dicts to access original confidence & snippet cleanly
    if isinstance(metrics, dict):
        metric_lookup = {str(k): v for k, v in metrics.items() if isinstance(v, dict)}
    elif isinstance(metrics, list):
        metric_lookup = {
            str(m.get("name") or m.get("metric")): m
            for m in metrics
            if isinstance(m, dict)
        }
    else:
        metric_lookup = {}

    results = analyze_company(metrics, thresholds=thresholds, company=company)
    new_validations = list(existing_validations)
    new_escalations = list(existing_escalations)

    for res in results:
        metric_name = str(res.get("metric") or "unknown")
        comp = res.get("company") or company
        period = res.get("current_period")
        key = _deterministic_key(comp, metric_name, period)

        src_metric = metric_lookup.get(metric_name, {})
        updated_conf = apply_yoy_to_confidence(
            src_metric,
            res,
            fail_multiplier=fail_mult,
            warn_multiplier=warn_mult,
        )
        enriched = {
            **res,
            "key": key,
            "adjusted_confidence": updated_conf,
        }

        if key not in seen_val_keys:
            seen_val_keys.add(key)
            new_validations.append(enriched)

        esc = to_escalation(res)
        if esc is not None and key not in seen_esc_keys:
            seen_esc_keys.add(key)
            new_escalations.append({**esc, "key": key})

    return {
        "validation_results": new_validations,
        "escalations": new_escalations,
    }
