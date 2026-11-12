"""Data models and configuration loader for the Accounting Equation Validation Engine."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")

CANONICAL_METRIC_NAMES: Dict[str, str] = {
    "A": "total_assets",
    "L": "total_liabilities",
    "E": "equity",
    "NCI": "noncontrolling_interest",
    "TE": "temporary_equity",
    "LE": "total_liabilities_and_equity",
    "AC": "current_assets",
    "ANC": "noncurrent_assets",
    "LC": "current_liabilities",
    "LNC": "noncurrent_liabilities",
}

REVERSE_METRIC_NAMES: Dict[str, str] = {v: k for k, v in CANONICAL_METRIC_NAMES.items()}


def _parse_yaml_scalar(token: str) -> Any:
    raw = token.strip()
    if not raw:
        return ""
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    lower = raw.lower()
    if lower in ("true", "yes"):
        return True
    if lower in ("false", "no"):
        return False
    if lower in ("null", "none", "~"):
        return None
    try:
        if "." not in raw and "e" not in lower:
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Minimal dependency-free parser for flat/nested key-value & list YAML files."""
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        if isinstance(loaded, dict):
            return loaded
    except ImportError:
        pass

    root: Dict[str, Any] = {}
    stack: List[Tuple[int, Any]] = [(-1, root)]

    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        idx += 1
        stripped = line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = stripped.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if content.startswith("- "):
            item_val = _parse_yaml_scalar(content[2:])
            if isinstance(parent, list):
                parent.append(item_val)
            continue

        if ":" in content:
            key, rest = content.split(":", 1)
            key = key.strip()
            rest = rest.strip()
            if rest == "":
                # Peek next non-empty line to see if child is a list or dict
                child: Any = {}
                for look_idx in range(idx, len(lines)):
                    next_line = lines[look_idx].split("#", 1)[0].rstrip()
                    if not next_line.strip():
                        continue
                    if next_line.strip().startswith("- "):
                        child = []
                    break
                if isinstance(parent, dict):
                    parent[key] = child
                    stack.append((indent, child))
            else:
                if isinstance(parent, dict):
                    parent[key] = _parse_yaml_scalar(rest)
    return root


def decimal_to_json_number(value: Optional[Decimal]) -> Optional[Any]:
    """Convert Decimal to int when exact, or float/string without NaN."""
    if value is None:
        return None
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def to_decimal(value: Any) -> Optional[Decimal]:
    """Normalize numeric strings, accounting brackets '(1,234)', ints, and Decimals."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError(f"Cannot convert boolean {value!r} to Decimal")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        text = value.strip()
        if not text or text in {"-", "--", "—", "–", "N/A", "n/a", "NM", "nm", "None", "null"}:
            return None
        negative = False
        if text.startswith("(") and text.endswith(")"):
            negative = True
            text = text[1:-1].strip()
        for sym in ("$", "€", "£", "¥", "US$", "USD", ",", " "):
            text = text.replace(sym, "")
        if not text:
            return None
        try:
            d = Decimal(text)
            return -d if negative else d
        except InvalidOperation as exc:
            raise ValueError(f"Invalid numeric string {value!r}") from exc
    raise ValueError(f"Unsupported numeric value type: {type(value).__name__}")


@dataclass
class ValidationConfig:
    tolerance_multiplier: int = 2
    allowed_scales: List[int] = field(default_factory=lambda: [1, 1000, 1000000, 1000000000])
    default_base_confidence: float = 0.88
    escalation_threshold: float = 0.75
    pass_delta: float = 0.05
    confidence_cap: float = 1.00
    confidence_floor: float = 0.00
    fail_single_blame_delta: float = -0.30
    fail_ambiguous_delta: float = -0.15
    cleared_delta: float = 0.00
    warn_delta: float = -0.05
    derived_delta: float = -0.05
    skipped_delta: float = 0.00
    enabled_formulas: List[str] = field(
        default_factory=lambda: ["F1", "F2", "F3", "F5", "F6", "F7", "F8"]
    )
    hypothesis_order: List[str] = field(
        default_factory=lambda: [
            "wrong_column",
            "missing_nci_or_te",
            "wrong_row",
            "scale_error",
            "sign_error",
            "digit_transposition",
        ]
    )
    min_nonzero_dollars: int = 1
    significant_gap_dollars: int = 1000000
    scale_ratio_expected: int = 1000
    scale_ratio_tolerance: float = 0.05
    persistence_mode: str = "callback_or_memory"
    default_output_dir: str = "validation/audit_logs"

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "ValidationConfig":
        conf = raw.get("confidence", {}) if isinstance(raw.get("confidence"), dict) else {}
        mat = raw.get("materiality", {}) if isinstance(raw.get("materiality"), dict) else {}
        pers = raw.get("persistence", {}) if isinstance(raw.get("persistence"), dict) else {}
        return cls(
            tolerance_multiplier=int(raw.get("tolerance_multiplier", 2)),
            allowed_scales=[int(s) for s in raw.get("allowed_scales", [1, 1000, 1000000, 1000000000])],
            default_base_confidence=float(conf.get("default_base_confidence", 0.88)),
            escalation_threshold=float(conf.get("escalation_threshold", 0.75)),
            pass_delta=float(conf.get("pass_delta", 0.05)),
            confidence_cap=float(conf.get("confidence_cap", 1.00)),
            confidence_floor=float(conf.get("confidence_floor", 0.00)),
            fail_single_blame_delta=float(conf.get("fail_single_blame_delta", -0.30)),
            fail_ambiguous_delta=float(conf.get("fail_ambiguous_delta", -0.15)),
            cleared_delta=float(conf.get("cleared_delta", 0.00)),
            warn_delta=float(conf.get("warn_delta", -0.05)),
            derived_delta=float(conf.get("derived_delta", -0.05)),
            skipped_delta=float(conf.get("skipped_delta", 0.00)),
            enabled_formulas=list(
                raw.get("enabled_formulas", ["F1", "F2", "F3", "F5", "F6", "F7", "F8"])
            ),
            hypothesis_order=list(
                raw.get(
                    "hypothesis_order",
                    [
                        "wrong_column",
                        "missing_nci_or_te",
                        "wrong_row",
                        "scale_error",
                        "sign_error",
                        "digit_transposition",
                    ],
                )
            ),
            min_nonzero_dollars=int(mat.get("min_nonzero_dollars", 1)),
            significant_gap_dollars=int(mat.get("significant_gap_dollars", 1000000)),
            scale_ratio_expected=int(mat.get("scale_ratio_expected", 1000)),
            scale_ratio_tolerance=float(mat.get("scale_ratio_tolerance", 0.05)),
            persistence_mode=str(pers.get("mode", "callback_or_memory")),
            default_output_dir=str(pers.get("default_output_dir", "validation/audit_logs")),
        )


def load_config(config: Optional[Any] = None) -> ValidationConfig:
    """Load ValidationConfig from a ValidationConfig instance, dict, or YAML path."""
    if isinstance(config, ValidationConfig):
        return config
    if isinstance(config, dict):
        return ValidationConfig.from_dict(config)
    path = Path(config) if config is not None else DEFAULT_CONFIG_PATH
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return ValidationConfig.from_dict(json.loads(text))
    return ValidationConfig.from_dict(_parse_simple_yaml(text))


@dataclass
class BalanceSheetPeriodInput:
    """One balance-sheet date column (current or prior year)."""

    period_end: Optional[str]
    A: Any = None
    L: Any = None
    E: Any = None
    NCI: Any = None
    TE: Any = None
    LE: Any = None
    AC: Any = None
    ANC: Any = None
    LC: Any = None
    LNC: Any = None
    scale: Optional[int] = None
    rows: Dict[str, Any] = field(default_factory=dict)
    prior_values: Dict[str, Any] = field(default_factory=dict)
    alt_candidates: Dict[str, List[Any]] = field(default_factory=dict)
    base_confidences: Dict[str, float] = field(default_factory=dict)
    metric_periods: Dict[str, str] = field(default_factory=dict)
    metric_snippets: Dict[str, str] = field(default_factory=dict)
    e_includes_nci: bool = False
    equity_reporting_basis: str = "parent_only"  # or "total_including_nci"
    is_consolidated: bool = True
    already_normalized: bool = False
    derived_L: bool = False
    statement_title: Optional[str] = None
    notes: List[str] = field(default_factory=list)


@dataclass
class BalanceSheetInput:
    """Normalized or pre-normalized balance-sheet input for a filing."""

    cik: str
    company: str
    accession: Optional[str] = None
    fiscal_year: Optional[int] = None
    form: Optional[str] = None
    current: Optional[BalanceSheetPeriodInput] = None
    prior: Optional[BalanceSheetPeriodInput] = None
    missing_extractor_fields: List[str] = field(default_factory=list)
    source_path: Optional[str] = None


@dataclass
class MetricDecision:
    confidence: float
    decision: str  # "auto_approve" | "escalate"
    reason: Optional[str] = None
    base_confidence: Optional[float] = None
    confidence_delta: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "confidence": round(self.confidence, 4),
            "decision": self.decision,
        }
        if self.reason:
            payload["reason"] = self.reason
        return payload


@dataclass
class CheckResult:
    cik: str
    company: str
    accession: Optional[str]
    period: Optional[str]
    period_role: str  # "current" | "prior"
    check_id: str
    formula_used: str
    status: str  # "pass" | "fail" | "warn" | "skipped"
    expected: Optional[Decimal]
    actual: Optional[Decimal]
    gap: Optional[Decimal]
    tolerance: Optional[Decimal]
    triangulation: Dict[str, str]
    blamed: Optional[str]
    blame_confidence: Optional[str]  # "high" | "low" | "ambiguous" | None
    hypotheses: List[str]
    suggested_value: Optional[Decimal]
    derived: bool
    explanation: str
    metric_decisions: Dict[str, MetricDecision]
    skip_reason: Optional[str] = None
    candidate_hypotheses: Dict[str, List[str]] = field(default_factory=dict)
    candidate_suggestions: Dict[str, Optional[Decimal]] = field(default_factory=dict)
    formula_variant_note: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "cik": self.cik,
            "company": self.company,
            "accession": self.accession,
            "period": self.period,
            "period_role": self.period_role,
            "check_id": self.check_id,
            "formula_used": self.formula_used,
            "status": self.status,
            "expected": decimal_to_json_number(self.expected),
            "actual": decimal_to_json_number(self.actual),
            "gap": decimal_to_json_number(self.gap),
            "tolerance": decimal_to_json_number(self.tolerance),
            "triangulation": dict(self.triangulation),
            "blamed": self.blamed,
            "blame_confidence": self.blame_confidence,
            "hypotheses": list(self.hypotheses),
            "suggested_value": decimal_to_json_number(self.suggested_value),
            "derived": self.derived,
            "explanation": self.explanation,
            "metric_decisions": {k: v.to_dict() for k, v in self.metric_decisions.items()},
        }
        if self.skip_reason:
            out["skip_reason"] = self.skip_reason
        if self.formula_variant_note:
            out["formula_variant_note"] = self.formula_variant_note
        if self.candidate_hypotheses:
            out["candidate_hypotheses"] = {k: list(v) for k, v in self.candidate_hypotheses.items()}
        if self.candidate_suggestions:
            out["candidate_suggestions"] = {
                k: decimal_to_json_number(v) for k, v in self.candidate_suggestions.items()
            }
        if self.notes:
            out["notes"] = list(self.notes)
        return out


@dataclass
class ValidationReport:
    cik: str
    company: str
    accession: Optional[str]
    fiscal_year: Optional[int]
    form: Optional[str]
    results: List[CheckResult]
    two_year_interpretation: str
    summary: Dict[str, Any]
    metric_decisions: Dict[str, MetricDecision]
    missing_extractor_fields: List[str] = field(default_factory=list)
    persisted_to: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cik": self.cik,
            "company": self.company,
            "accession": self.accession,
            "fiscal_year": self.fiscal_year,
            "form": self.form,
            "two_year_interpretation": self.two_year_interpretation,
            "summary": dict(self.summary),
            "metric_decisions": {k: v.to_dict() for k, v in self.metric_decisions.items()},
            "results": [r.to_dict() for r in self.results],
            "missing_extractor_fields": list(self.missing_extractor_fields),
            "persisted_to": self.persisted_to,
        }

