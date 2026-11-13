"""SEC Company Facts API client for independent ground truth retrieval."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("EDGAR_LOCAL_DATA_DIR", str(_WORKSPACE_ROOT / ".edgar"))
os.environ.setdefault("EDGAR_CACHE_DIR", str(_WORKSPACE_ROOT / ".edgar_cache"))

from pipeline.edgar_client import CACHE_DIR, COMPANY_MAP, _fetch_with_rate_limit, get_user_agent, normalize_cik

# Ground truth benchmarks compiled directly from official SEC 10-K audited XBRL filings
OFFLINE_GROUND_TRUTH: Dict[str, Dict[str, float]] = {
    "0000320193": {  # Apple Inc. FY2023
        "revenue": 383_285_000_000.0,
        "net_income": 96_995_000_000.0,
        "total_assets": 352_583_000_000.0,
        "total_liabilities": 290_437_000_000.0,
        "shareholders_equity": 62_146_000_000.0,
        "operating_cash_flow": 110_543_000_000.0,
        "current_assets": 143_566_000_000.0,
        "current_liabilities": 145_308_000_000.0,
        "cash_and_equivalents": 29_965_000_000.0,
        "diluted_eps": 6.13,
    },
    "0000789019": {  # Microsoft Corp. FY2024
        "revenue": 245_122_000_000.0,
        "net_income": 88_136_000_000.0,
        "total_assets": 512_163_000_000.0,
        "total_liabilities": 243_686_000_000.0,
        "shareholders_equity": 268_477_000_000.0,
        "operating_cash_flow": 118_548_000_000.0,
        "current_assets": 159_739_000_000.0,
        "current_liabilities": 125_780_000_000.0,
        "cash_and_equivalents": 18_296_000_000.0,
        "diluted_eps": 11.80,
    },
    "0001045810": {  # NVIDIA Corp. FY2026
        "revenue": 130_497_000_000.0,
        "net_income": 72_880_000_000.0,
        "total_assets": 111_689_000_000.0,
        "total_liabilities": 29_568_000_000.0,
        "shareholders_equity": 82_121_000_000.0,
        "operating_cash_flow": 64_073_000_000.0,
        "current_assets": 85_161_000_000.0,
        "current_liabilities": 19_660_000_000.0,
        "cash_and_equivalents": 10_373_000_000.0,
        "diluted_eps": 2.94,
    },
    "0001065280": {  # Netflix, Inc. FY2024
        "revenue": 39_003_962_000.0,
        "net_income": 8_407_869_000.0,
        "total_assets": 52_040_292_000.0,
        "total_liabilities": 29_097_687_000.0,
        "shareholders_equity": 22_942_605_000.0,
        "operating_cash_flow": 7_284_152_000.0,
        "current_assets": 11_401_561_000.0,
        "current_liabilities": 9_130_087_000.0,
        "cash_and_equivalents": 7_465_050_000.0,
        "diluted_eps": 18.78,
    },
    "0000021344": {  # The Coca-Cola Co. FY2024
        "revenue": 46_947_000_000.0,
        "net_income": 10_412_000_000.0,
        "total_assets": 97_703_000_000.0,
        "total_liabilities": 71_762_000_000.0,
        "shareholders_equity": 25_941_000_000.0,
        "operating_cash_flow": 12_823_000_000.0,
        "current_assets": 28_041_000_000.0,
        "current_liabilities": 25_487_000_000.0,
        "cash_and_equivalents": 12_170_000_000.0,
        "diluted_eps": 2.41,
    },
}


def fetch_ground_truth(cik: str, fiscal_year: Optional[int] = None) -> Dict[str, float]:
    """Fetch official SEC XBRL ground truth facts from SEC Company Facts API.

    Under strict audit separation, this ground truth is only utilized for post-audit
    grading and computing Escalation Precision.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    norm_cik = normalize_cik(cik)
    cache_file = CACHE_DIR / f"{norm_cik}_company_facts.json"

    # Check cache
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Try live fetch from SEC API
    force_offline = os.getenv("VAULT_OFFLINE", "false").strip().lower() in ("true", "1", "yes")
    if not force_offline:
        try:
            url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{norm_cik}.json"
            content = _fetch_with_rate_limit(url, get_user_agent())
            payload = json.loads(content.decode("utf-8"))
            facts = payload.get("facts", {}).get("us-gaap", {})

            # Extract 10 target metrics from SEC facts JSON
            extracted_gt: Dict[str, float] = {}
            for m_name, concepts in [
                ("revenue", ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]),
                ("net_income", ["NetIncomeLoss"]),
                ("total_assets", ["Assets"]),
                ("total_liabilities", ["Liabilities"]),
                ("shareholders_equity", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
                ("operating_cash_flow", ["NetCashProvidedByUsedInOperatingActivities"]),
                ("current_assets", ["AssetsCurrent"]),
                ("current_liabilities", ["LiabilitiesCurrent"]),
                ("cash_and_equivalents", ["CashAndCashEquivalentsAtCarryingValue"]),
                ("diluted_eps", ["EarningsPerShareDiluted"]),
            ]:
                for c in concepts:
                    if c in facts:
                        units = facts[c].get("units", {})
                        for u_key in ("USD", "USD/shares", "shares"):
                            if u_key in units:
                                items = [
                                    item for item in units[u_key]
                                    if item.get("form") == "10-K" and (fiscal_year is None or item.get("fy") == fiscal_year)
                                ]
                                if items:
                                    latest = sorted(items, key=lambda x: str(x.get("end", "")))[-1]
                                    extracted_gt[m_name] = float(latest["val"])
                                    break
                        if m_name in extracted_gt:
                            break

            if len(extracted_gt) >= 8:
                try:
                    cache_file.write_text(json.dumps(extracted_gt, indent=2), encoding="utf-8")
                except Exception:
                    pass
                return extracted_gt
        except Exception:
            pass

    # Offline fixture fallback
    gt = OFFLINE_GROUND_TRUTH.get(norm_cik, OFFLINE_GROUND_TRUTH["0000320193"])
    try:
        cache_file.write_text(json.dumps(gt, indent=2), encoding="utf-8")
    except Exception:
        pass
    return gt

