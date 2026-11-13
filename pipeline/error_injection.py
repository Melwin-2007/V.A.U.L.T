"""Deliberate error injection engine for live judge demonstrations and resilience benchmarking."""

from __future__ import annotations

import copy
from typing import Any, Dict, List


INJECTION_TYPES = [
    "scale_1000x",
    "sign_flip",
    "wrong_column",
    "missing_digit",
    "swapped_assets_liabilities",
]


def apply_error_injection(records: List[Dict[str, Any]], inject_type: str) -> List[Dict[str, Any]]:
    """Inject deliberate, labeled corruptions into extracted metrics to prove auditor vigilance.

    Every corrupted record is marked with injected_error=True for escalation precision calculation.
    """
    if not inject_type or inject_type.lower() in ("none", "false"):
        return records

    corrupted = copy.deepcopy(records)
    by_name = {m["metric_name"]: m for m in corrupted}

    kind = inject_type.lower().strip()

    if kind == "scale_1000x":
        target = by_name.get("revenue") or by_name.get("total_assets")
        if target and target.get("value") is not None:
            target["value"] = target["value"] * 1000.0
            target["confidence_breakdown"]["yoy"] = 0.3
            target["confidence_breakdown"]["grounding"] = 0.0
            target["injected_error"] = True

    elif kind == "sign_flip":
        target = by_name.get("net_income") or by_name.get("operating_cash_flow")
        if target and target.get("value") is not None:
            target["value"] = -abs(target["value"])
            target["confidence_breakdown"]["yoy"] = 0.3
            target["confidence_breakdown"]["grounding"] = 0.0
            target["injected_error"] = True

    elif kind == "wrong_column":
        # Prior year value picked instead of current period
        target = by_name.get("total_liabilities") or by_name.get("revenue")
        if target:
            if target.get("prior_value") is not None:
                target["value"] = float(target["prior_value"])
            else:
                target["value"] = (target.get("value") or 100_000_000.0) * 0.88
            target["confidence_breakdown"]["grounding"] = 0.0
            target["injected_error"] = True

    elif kind == "missing_digit":
        # Drop a digit while keeping the snippet unchanged (tests verbatim grounding)
        target = by_name.get("total_assets") or by_name.get("revenue")
        if target and target.get("value") is not None:
            val_str = str(int(target["value"]))
            if len(val_str) > 4:
                # Drop second digit
                dropped_str = val_str[0] + val_str[2:]
                target["value"] = float(dropped_str)
            else:
                target["value"] = target["value"] / 10.0
            target["confidence_breakdown"]["grounding"] = 0.0
            target["injected_error"] = True

    elif kind == "swapped_assets_liabilities":
        # Swap total_assets and total_liabilities (catches hard accounting equation & subtotal inversion)
        a = by_name.get("total_assets")
        l = by_name.get("total_liabilities")
        if a and l and a.get("value") is not None and l.get("value") is not None:
            temp = a["value"]
            a["value"] = l["value"]
            l["value"] = temp
            a["injected_error"] = True
            l["injected_error"] = True

    return corrupted

