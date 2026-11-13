"""SEC EDGAR client with polite rate limiting, disk caching, and offline fixture fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Dict, Optional
import urllib.error
import urllib.request

# Ensure edgartools uses workspace local directory in Windows sandbox
_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("EDGAR_LOCAL_DATA_DIR", str(_WORKSPACE_ROOT / ".edgar"))
os.environ.setdefault("EDGAR_CACHE_DIR", str(_WORKSPACE_ROOT / ".edgar_cache"))

CACHE_DIR = _WORKSPACE_ROOT / "data" / "cache"
FIXTURES_DIR = _WORKSPACE_ROOT / "edgartools" / "tests" / "fixtures" / "xbrl"

COMPANY_MAP: Dict[str, Dict[str, Any]] = {
    "0000320193": {
        "ticker": "AAPL",
        "name": "Apple Inc.",
        "fixture_dir": FIXTURES_DIR / "aapl" / "10k_2023",
        "fiscal_year": 2023,
        "accession": "0000320193-23-000106",
        "filing_date": "2023-11-03",
    },
    "0000789019": {
        "ticker": "MSFT",
        "name": "Microsoft Corporation",
        "fixture_dir": FIXTURES_DIR / "msft" / "10k_2024",
        "fiscal_year": 2024,
        "accession": "0000950170-24-087843",
        "filing_date": "2024-07-30",
    },
    "0001045810": {
        "ticker": "NVDA",
        "name": "NVIDIA Corporation",
        "fixture_dir": FIXTURES_DIR / "nvda" / "10k_2026",
        "fiscal_year": 2026,
        "accession": "0001045810-26-000021",
        "filing_date": "2026-02-25",
    },
    "0001065280": {
        "ticker": "NFLX",
        "name": "Netflix, Inc.",
        "fixture_dir": FIXTURES_DIR / "nflx" / "10k_2024",
        "fiscal_year": 2024,
        "accession": "0001065280-24-000030",
        "filing_date": "2024-01-26",
    },
    "0000021344": {
        "ticker": "KO",
        "name": "The Coca-Cola Company",
        "fixture_dir": FIXTURES_DIR / "ko" / "10k_2024",
        "fiscal_year": 2024,
        "accession": "0000021344-24-000009",
        "filing_date": "2024-02-20",
    },
}

# Aliases for ticker lookup
TICKER_MAP = {info["ticker"].lower(): cik for cik, info in COMPANY_MAP.items()}


def normalize_cik(cik_or_ticker: str) -> str:
    cleaned = str(cik_or_ticker).strip()
    if cleaned.lower() in TICKER_MAP:
        return TICKER_MAP[cleaned.lower()]
    digits = "".join(c for c in cleaned if c.isdigit())
    if not digits:
        raise ValueError(f"Invalid CIK or ticker: {cik_or_ticker}")
    return digits.zfill(10)


def get_user_agent() -> str:
    return os.getenv("SEC_USER_AGENT", "VaultAuditor team@vault.internal")


def _fetch_with_rate_limit(url: str, user_agent: str, max_retries: int = 3) -> bytes:
    """Fetch URL with polite backoff and proper headers adhering to SEC limits."""
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    req = urllib.request.Request(url, headers=headers)

    for attempt in range(max_retries):
        try:
            # Respect SEC 10 req/sec rate limit
            time.sleep(0.12)
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts.")


def fetch_filing(cik: str, fiscal_year: Optional[int] = None) -> Dict[str, Any]:
    """Fetch 10-K filing metadata and contents with disk cache and fixture fallback.

    Returns:
        Dict conforming to docs/CONTRACT.md filing representation.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    norm_cik = normalize_cik(cik)
    meta = COMPANY_MAP.get(norm_cik)

    company_name = meta["name"] if meta else f"Company CIK {norm_cik}"
    year = fiscal_year or (meta["fiscal_year"] if meta else 2024)
    fixture_dir = meta["fixture_dir"] if meta else None

    # Check disk cache first
    cache_file = CACHE_DIR / f"{norm_cik}_{year}_10K.json"
    if cache_file.exists():
        try:
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            cached_data["cached"] = True
            return cached_data
        except Exception:
            pass

    # Try live fetch if network permitted and not explicitly offline
    force_offline = os.getenv("VAULT_OFFLINE", "false").strip().lower() in ("true", "1", "yes")
    live_success = False
    filing_record: Dict[str, Any] = {}

    if not force_offline:
        try:
            sec_url = f"https://data.sec.gov/submissions/CIK{norm_cik}.json"
            data = _fetch_with_rate_limit(sec_url, get_user_agent())
            payload = json.loads(data.decode("utf-8"))
            recent = payload.get("filings", {}).get("recent", {})
            forms = recent.get("form", [])
            accessions = recent.get("accessionNumber", [])
            filing_dates = recent.get("filingDate", [])

            # Locate latest 10-K
            for idx, form in enumerate(forms):
                if form == "10-K":
                    acc = accessions[idx]
                    fdate = filing_dates[idx]
                    filing_record = {
                        "cik": norm_cik,
                        "company": payload.get("name", company_name),
                        "fiscal_year": year,
                        "form": "10-K",
                        "accession_no": acc,
                        "filing_date": fdate,
                        "source": "edgar_live",
                        "xbrl_dir": str(fixture_dir) if (fixture_dir and fixture_dir.exists()) else "",
                        "sec_url": f"https://www.sec.gov/edgar/data/{int(norm_cik)}/{acc.replace('-', '')}",
                    }
                    live_success = True
                    break
        except Exception:
            live_success = False

    # Fallback to local offline fixture
    if not live_success:
        if fixture_dir and fixture_dir.exists():
            filing_record = {
                "cik": norm_cik,
                "company": meta["name"],
                "fiscal_year": year,
                "form": "10-K",
                "accession_no": meta["accession"],
                "filing_date": meta["filing_date"],
                "source": "offline_fixture",
                "xbrl_dir": str(fixture_dir),
                "sec_url": f"https://www.sec.gov/edgar/data/{int(norm_cik)}/{meta['accession'].replace('-', '')}",
            }
        else:
            # Fallback for generic CIK
            filing_record = {
                "cik": norm_cik,
                "company": company_name,
                "fiscal_year": year,
                "form": "10-K",
                "accession_no": f"{norm_cik}-23-000001",
                "filing_date": f"{year}-12-31",
                "source": "offline_synthetic",
                "xbrl_dir": "",
                "sec_url": f"https://www.sec.gov/edgar/data/{int(norm_cik)}/0000000000",
            }

    # Save to disk cache
    try:
        cache_file.write_text(json.dumps(filing_record, indent=2), encoding="utf-8")
    except Exception:
        pass

    return filing_record

