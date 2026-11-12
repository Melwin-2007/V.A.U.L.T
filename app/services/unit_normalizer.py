"""Deterministic unit parsing and normalization for financial metrics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from app.models.yoy_models import UnitNormalizationRecord, UnitNormalizationSummary


class UnitNormalizationError(ValueError):
    """Raised when units are missing, unrecognized, or incompatible."""


@dataclass(frozen=True)
class ParsedUnit:
    """Canonical representation of a financial unit."""

    dimension: str
    scale_name: str
    multiplier: Decimal
    canonical_name: str


_CURRENCY_PATTERNS: list[tuple[str, str]] = [
    ("USD", r"(?:\bus\s*dollars?\b|\busd\b|\bus\$|\bdollars?\b|\$)"),
    ("EUR", r"(?:\beuros?\b|\beur\b|€)"),
    ("GBP", r"(?:\bpounds?\s*sterling\b|\bpounds?\b|\bgbp\b|£)"),
    ("JPY", r"(?:\byen\b|\bjpy\b|¥)"),
    ("CAD", r"(?:\bcad\b|\bc\$)"),
    ("CHF", r"(?:\bchf\b)"),
    ("INR", r"(?:\binr\b|\brupees?\b|₹)"),
    ("CNY", r"(?:\bcny\b|\brmb\b)"),
]

_SCALE_MAP: list[tuple[str, Decimal, str]] = [
    (r"(?:\btrillions?\b|\btn\b|\bt\b)", Decimal("1000000000000"), "trillions"),
    (r"(?:\bbillions?\b|\bbn\b|\bb\b)", Decimal("1000000000"), "billions"),
    (r"(?:\bmillions?\b|\bmm\b|\bmn\b|\bm\b)", Decimal("1000000"), "millions"),
    (r"(?:\bthousands?\b|\bk\b)", Decimal("1000"), "thousands"),
    (r"(?:\bunits?\b|\bexact\b)", Decimal("1"), ""),
]

_PER_SHARE_RE = re.compile(r"(?:\bper\s+share\b|/\s*shares?\b)")
_SHARES_RE = re.compile(r"\bshares?\b")
_BPS_RE = re.compile(r"(?:\bbasis\s+points?\b|\bbps\b|\bbp\b)")
_PERCENT_RE = re.compile(r"(?:%|\bpercent(?:age)?\b|\bpct\b)")


def _detect_currency(text: str) -> Optional[str]:
    for code, pattern in _CURRENCY_PATTERNS:
        if re.search(pattern, text):
            return code
    return None


def _strip_currency_tokens(text: str) -> str:
    cleaned = text
    for _, pattern in _CURRENCY_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned)
    return cleaned


def _detect_scale(text_without_currency: str) -> tuple[Decimal, str, bool]:
    """Return (multiplier, scale_name, explicit_scale_found)."""
    for pattern, multiplier, scale_name in _SCALE_MAP:
        if re.search(pattern, text_without_currency):
            return multiplier, scale_name, True
    return Decimal("1"), "", False


def _canonical_name(dimension: str, scale_name: str) -> str:
    if dimension == "PERCENT":
        return "bps" if scale_name == "bps" else "percent"
    if dimension.endswith("_PER_SHARE"):
        currency = dimension.removesuffix("_PER_SHARE")
        base = f"{currency} per share" if currency != "GENERIC" else "per share"
        return f"{base} ({scale_name})" if scale_name else base
    if dimension == "SHARES":
        return f"shares ({scale_name})" if scale_name else "shares"
    if dimension == "GENERIC":
        return scale_name if scale_name else "units"
    return f"{dimension} {scale_name}".strip()


def parse_unit(raw_unit: Optional[str]) -> ParsedUnit:
    """Parse a unit string into a canonical dimension and scale multiplier."""
    if raw_unit is None or not isinstance(raw_unit, str) or not raw_unit.strip():
        raise UnitNormalizationError("Unit is missing or empty.")

    normalized_text = re.sub(r"\s+", " ", raw_unit.strip().lower())

    # Reject composite values like "$500 million" embedded inside a unit field?
    # Actually, handle unit strings cleanly while guarding against pure numbers.
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", normalized_text):
        raise UnitNormalizationError(f"Unrecognized numeric-only unit: {raw_unit!r}.")

    if _BPS_RE.search(normalized_text):
        return ParsedUnit(
            dimension="PERCENT",
            scale_name="bps",
            multiplier=Decimal("0.01"),
            canonical_name="bps",
        )

    if _PERCENT_RE.search(normalized_text):
        return ParsedUnit(
            dimension="PERCENT",
            scale_name="percent",
            multiplier=Decimal("1"),
            canonical_name="percent",
        )

    currency = _detect_currency(normalized_text)
    is_per_share = bool(_PER_SHARE_RE.search(normalized_text))
    has_shares = bool(_SHARES_RE.search(normalized_text))

    stripped = _strip_currency_tokens(normalized_text)
    stripped = _PER_SHARE_RE.sub(" ", stripped)
    stripped = _SHARES_RE.sub(" ", stripped)
    stripped = re.sub(r"\bof\b|\bin\b|[()]", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()

    multiplier, scale_name, explicit_scale = _detect_scale(stripped)

    # Check if leftover unknown words exist after removing recognized tokens.
    leftover = stripped
    for pattern, _, _ in _SCALE_MAP:
        leftover = re.sub(pattern, " ", leftover)
    leftover = re.sub(r"[\s,._-]+", "", leftover)
    if leftover:
        raise UnitNormalizationError(f"Unrecognized or ambiguous unit: {raw_unit!r}.")

    if is_per_share:
        dim = f"{currency}_PER_SHARE" if currency else "GENERIC_PER_SHARE"
    elif currency:
        dim = currency
    elif has_shares:
        dim = "SHARES"
    elif explicit_scale:
        dim = "GENERIC"
    else:
        raise UnitNormalizationError(f"Unrecognized or ambiguous unit: {raw_unit!r}.")

    canonical = _canonical_name(dim, scale_name)
    return ParsedUnit(
        dimension=dim,
        scale_name=scale_name,
        multiplier=multiplier,
        canonical_name=canonical,
    )


def normalize_pair(
    current_value: Decimal,
    current_unit: Optional[str],
    previous_value: Decimal,
    previous_unit: Optional[str],
) -> tuple[Decimal, Decimal, str, UnitNormalizationSummary]:
    """Normalize current and previous values to a common compatible unit.

    When two compatible units have different scales (e.g. USD millions vs USD
    billions), both are converted to the finer (smaller) scale to keep exact
    Decimal arithmetic clear and readable.
    """
    parsed_curr = parse_unit(current_unit)
    parsed_prev = parse_unit(previous_unit)

    if parsed_curr.dimension != parsed_prev.dimension:
        raise UnitNormalizationError(
            f"Incompatible units: current year uses {current_unit!r} "
            f"({parsed_curr.dimension}) while previous year uses {previous_unit!r} "
            f"({parsed_prev.dimension})."
        )

    # Choose the finer (smaller multiplier) scale as the target common scale.
    target = parsed_curr if parsed_curr.multiplier <= parsed_prev.multiplier else parsed_prev

    curr_factor = parsed_curr.multiplier / target.multiplier
    prev_factor = parsed_prev.multiplier / target.multiplier

    norm_curr_val = current_value * curr_factor
    norm_prev_val = previous_value * prev_factor

    curr_converted = curr_factor != Decimal("1")
    prev_converted = prev_factor != Decimal("1")

    summary = UnitNormalizationSummary(
        current_year=UnitNormalizationRecord(
            original_unit=current_unit,
            normalized_unit=target.canonical_name,
            conversion_applied=curr_converted,
            original_value=float(current_value),
            normalized_value=float(norm_curr_val),
        ),
        previous_year=UnitNormalizationRecord(
            original_unit=previous_unit,
            normalized_unit=target.canonical_name,
            conversion_applied=prev_converted,
            original_value=float(previous_value),
            normalized_value=float(norm_prev_val),
        ),
    )
    return norm_curr_val, norm_prev_val, target.canonical_name, summary
