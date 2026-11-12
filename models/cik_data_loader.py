"""
CIK-only Data Loader for V.A.U.L.T Accounting Equation Engine.

Given ONLY a Central Index Key (CIK), resolves company metadata and extracts
current-year & prior-year Balance Sheet periods directly from data already
present in the repository:
  1. Reference Parquet tables:
       - edgartools/edgar/reference/data/company_tickers.parquet
       - edgartools/tests/fixtures/companies_sample.pq
  2. Local SEC XBRL filing fixtures:
       - edgartools/tests/fixtures/xbrl/<ticker>/.../*.xml
  3. Local SEC CompanyFacts JSON fixtures:
       - edgartools/tests/fixtures/entity/*_facts.json
  4. Optional live SEC EDGAR CompanyFacts API fallback (when allow_network=True
     and the CIK is not present in the local repository).
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import pandas as pd

from models.accounting_equation import BalanceSheetPeriodInput


def normalize_cik(cik: Union[int, str]) -> Tuple[str, int]:
    """Normalize CIK input (e.g., 320193, '320193', '0000320193') -> ('0000320193', 320193)."""
    cleaned = re.sub(r"\D", "", str(cik).strip())
    if not cleaned:
        raise ValueError(f"Invalid CIK value: {cik!r}")
    cik_int = int(cleaned)
    return f"{cik_int:010d}", cik_int


def _split_camel_case(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name).strip()


def _decimals_to_scale(decimals: Optional[str]) -> float:
    """Convert XBRL decimals attribute (e.g., '-6', '-3', '0') to reporting scale."""
    if decimals is None:
        return 1_000_000.0
    s = str(decimals).strip().upper()
    if s in ("INF", ""):
        return 1.0
    try:
        d = int(s)
    except ValueError:
        return 1_000_000.0
    if d <= -9:
        return 1_000_000_000.0
    if d <= -6:
        return 1_000_000.0
    if d <= -3:
        return 1_000.0
    if d < 0:
        return float(10 ** (-d))
    return 1.0


def _infer_scale_from_values(values: List[float]) -> float:
    """Infer table reporting scale from trailing zeros when decimals metadata is absent."""
    non_zero = [abs(int(round(v))) for v in values if v is not None and abs(v) >= 1000]
    if not non_zero:
        return 1.0
    if all(v % 1_000_000 == 0 for v in non_zero):
        return 1_000_000.0
    if all(v % 1_000 == 0 for v in non_zero):
        return 1_000.0
    return 1.0


@dataclass
class LocalFilingRecord:
    cik10: str
    cik_int: int
    company_name: str
    ticker: str
    source_type: str  # "xbrl_directory" | "entity_facts_json" | "sec_api"
    path: Path
    form: str = "10-K"
    filing_date: str = ""
    doc_period_end: str = ""


@dataclass
class CompanyBalanceSheetDataset:
    cik: str
    cik_int: int
    company_name: str
    ticker: str
    exchange: str
    sic_description: str
    source_type: str
    source_path: str
    form: str
    filing_date: str
    current_period: BalanceSheetPeriodInput
    prior_period: Optional[BalanceSheetPeriodInput]
    all_periods: List[BalanceSheetPeriodInput] = field(default_factory=list)


class RepoCIKDataLoader:
    """
    Loads company metadata and Balance Sheet periods from the repository using ONLY a CIK.
    """

    def __init__(self, repo_root: Optional[Union[str, Path]] = None) -> None:
        if repo_root is None:
            # Default: parent of `models/`
            self.repo_root = Path(__file__).resolve().parent.parent
        else:
            self.repo_root = Path(repo_root).resolve()

        self.edgar_root = self.repo_root / "edgartools"
        self.xbrl_fixtures_dir = self.edgar_root / "tests" / "fixtures" / "xbrl"
        self.entity_fixtures_dir = self.edgar_root / "tests" / "fixtures" / "entity"
        self.tickers_parquet = self.edgar_root / "edgar" / "reference" / "data" / "company_tickers.parquet"
        self.companies_sample_pq = self.edgar_root / "tests" / "fixtures" / "companies_sample.pq"

        self._ticker_lookup_df: Optional[pd.DataFrame] = None
        self._companies_sample_df: Optional[pd.DataFrame] = None
        self._filing_index: Optional[List[LocalFilingRecord]] = None

    # ------------------------------------------------------------------
    # Reference Table Lookups
    # ------------------------------------------------------------------
    def _load_reference_tables(self) -> None:
        if self._ticker_lookup_df is None and self.tickers_parquet.exists():
            df = pd.read_parquet(self.tickers_parquet)
            df["cik10"] = df["cik"].astype(str).str.strip().str.zfill(10)
            df["ticker_lower"] = df["ticker"].astype(str).str.strip().str.lower()
            self._ticker_lookup_df = df

        if self._companies_sample_df is None and self.companies_sample_pq.exists():
            df2 = pd.read_parquet(self.companies_sample_pq)
            df2["cik10"] = df2["cik"].astype(str).str.strip().str.zfill(10)
            self._companies_sample_df = df2

    def lookup_company_metadata(self, cik: Union[int, str]) -> Dict[str, str]:
        """Resolve company name, ticker, exchange, SIC description from offline repo tables."""
        cik10, cik_int = normalize_cik(cik)
        self._load_reference_tables()

        meta = {
            "cik": cik10,
            "cik_int": str(cik_int),
            "name": f"CIK {cik10}",
            "ticker": "",
            "exchange": "",
            "sic_description": "",
        }

        if self._ticker_lookup_df is not None:
            hits = self._ticker_lookup_df[self._ticker_lookup_df["cik10"] == cik10]
            if not hits.empty:
                row = hits.iloc[0]
                meta["name"] = str(row.get("name") or meta["name"])
                meta["ticker"] = str(row.get("ticker") or "")
                meta["exchange"] = str(row.get("exchange") or "")

        if self._companies_sample_df is not None:
            hits2 = self._companies_sample_df[self._companies_sample_df["cik10"] == cik10]
            if not hits2.empty:
                row = hits2.iloc[0]
                if meta["name"].startswith("CIK ") and row.get("name"):
                    meta["name"] = str(row["name"])
                if not meta["ticker"] and row.get("tickers"):
                    tval = row["tickers"]
                    if isinstance(tval, (list, tuple)) and len(tval) > 0:
                        meta["ticker"] = str(tval[0])
                    elif isinstance(tval, str) and tval:
                        meta["ticker"] = tval.strip("[]'\" ").split(",")[0].strip("'\" ")
                if row.get("sic_description"):
                    meta["sic_description"] = str(row["sic_description"])

        return meta

    # ------------------------------------------------------------------
    # Repository Indexing (XBRL fixtures + Entity Facts JSONs)
    # ------------------------------------------------------------------
    @staticmethod
    def _find_instance_xml(folder: Path) -> Optional[Path]:
        htm_xmls = sorted(folder.glob("*_htm.xml"))
        if htm_xmls:
            return htm_xmls[0]
        candidates = [
            p
            for p in sorted(folder.glob("*.xml"))
            if not p.name.endswith(("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", ".xsd.xml"))
            and p.name != "ix.xml"
        ]
        return candidates[0] if candidates else None

    @staticmethod
    def _extract_dei_quick(xml_path: Path) -> Dict[str, str]:
        """Fast streaming/scan extraction of DEI metadata from an XBRL instance XML."""
        dei: Dict[str, str] = {}
        wanted = {
            "EntityCentralIndexKey",
            "EntityRegistrantName",
            "DocumentType",
            "DocumentPeriodEndDate",
            "TradingSymbol",
        }
        try:
            for event, elem in ET.iterparse(xml_path, events=("end",)):
                local = elem.tag.split("}")[-1]
                if local in wanted and elem.text:
                    dei.setdefault(local, elem.text.strip())
                # Stop early once we reach contexts/facts past DEI if all found
                if len(dei) >= 4:
                    break
                elem.clear()
        except Exception:
            pass

        if "EntityCentralIndexKey" not in dei:
            # Fallback full parse in case DEI tags are lower in the file
            try:
                root = ET.parse(xml_path).getroot()
                for elem in root:
                    local = elem.tag.split("}")[-1]
                    if local in wanted and elem.text and local not in dei:
                        dei[local] = elem.text.strip()
            except Exception:
                pass
        return dei

    def build_index(self, force_rebuild: bool = False) -> List[LocalFilingRecord]:
        """Discover all filings in the repository indexed by CIK."""
        if self._filing_index is not None and not force_rebuild:
            return self._filing_index

        self._load_reference_tables()
        records: List[LocalFilingRecord] = []

        # 1. Index XBRL fixture directories
        if self.xbrl_fixtures_dir.exists():
            candidate_dirs = sorted(
                {
                    p.parent
                    for p in list(self.xbrl_fixtures_dir.rglob("*_htm.xml"))
                    + [
                        x
                        for x in self.xbrl_fixtures_dir.rglob("*.xml")
                        if not x.name.endswith(("_cal.xml", "_def.xml", "_lab.xml", "_pre.xml", ".xsd.xml"))
                    ]
                }
            )
            for folder in candidate_dirs:
                inst = self._find_instance_xml(folder)
                if inst is None:
                    continue

                meta_json = None
                for mname in ("metadata.json", "manifest.json"):
                    mp = folder / mname
                    if mp.exists():
                        try:
                            meta_json = json.loads(mp.read_text(encoding="utf-8"))
                        except Exception:
                            meta_json = None
                        break

                cik_raw = meta_json.get("cik") if meta_json else None
                form = str(meta_json.get("form", "")) if meta_json else ""
                filing_date = str(meta_json.get("filing_date", "")) if meta_json else ""
                company_name = str(meta_json.get("company", "")) if meta_json else ""
                ticker = str(meta_json.get("ticker", "")) if meta_json else ""

                dei = self._extract_dei_quick(inst)
                if not cik_raw:
                    cik_raw = dei.get("EntityCentralIndexKey")
                if not company_name:
                    company_name = dei.get("EntityRegistrantName", "")
                if not form:
                    form = dei.get("DocumentType", "")
                doc_period_end = dei.get("DocumentPeriodEndDate", "")
                if not ticker:
                    ticker = dei.get("TradingSymbol", "")

                # Infer ticker from folder structure if still empty
                if not ticker:
                    rel = folder.relative_to(self.xbrl_fixtures_dir)
                    parts = rel.parts
                    if parts[0] == "special_cases" and len(parts) >= 3:
                        ticker = parts[2].upper()
                    else:
                        ticker = parts[0].upper()

                if not cik_raw and self._ticker_lookup_df is not None and ticker:
                    hits = self._ticker_lookup_df[
                        self._ticker_lookup_df["ticker_lower"] == ticker.lower()
                    ]
                    if not hits.empty:
                        cik_raw = hits.iloc[0]["cik10"]

                if not cik_raw:
                    continue

                try:
                    cik10, cik_int = normalize_cik(cik_raw)
                except ValueError:
                    continue

                if not form:
                    fname = folder.name.lower()
                    if "10q" in fname:
                        form = "10-Q"
                    else:
                        form = "10-K"

                if not company_name:
                    company_name = self.lookup_company_metadata(cik10)["name"]

                records.append(
                    LocalFilingRecord(
                        cik10=cik10,
                        cik_int=cik_int,
                        company_name=company_name,
                        ticker=ticker,
                        source_type="xbrl_directory",
                        path=folder,
                        form=form,
                        filing_date=filing_date,
                        doc_period_end=doc_period_end,
                    )
                )

        # 2. Index entity/*_facts.json fixtures
        if self.entity_fixtures_dir.exists():
            for jf in sorted(self.entity_fixtures_dir.glob("*.json")):
                try:
                    # Read header quickly or full json
                    data = json.loads(jf.read_text(encoding="utf-8"))
                    cik_raw = data.get("cik")
                    if not cik_raw:
                        continue
                    cik10, cik_int = normalize_cik(cik_raw)
                    name = str(data.get("entityName") or self.lookup_company_metadata(cik10)["name"])
                    ticker = self.lookup_company_metadata(cik10)["ticker"] or jf.stem.replace("_facts", "").upper()
                    records.append(
                        LocalFilingRecord(
                            cik10=cik10,
                            cik_int=cik_int,
                            company_name=name,
                            ticker=ticker,
                            source_type="entity_facts_json",
                            path=jf,
                            form="10-K",
                            filing_date="",
                            doc_period_end="",
                        )
                    )
                except Exception:
                    continue

        self._filing_index = records
        return records

    def list_available_ciks(self) -> List[Dict[str, Any]]:
        """List all unique CIKs with balance sheet data available offline in the repo."""
        index = self.build_index()
        by_cik: Dict[str, Dict[str, Any]] = {}
        for rec in index:
            entry = by_cik.setdefault(
                rec.cik10,
                {
                    "cik": rec.cik10,
                    "cik_int": rec.cik_int,
                    "company_name": rec.company_name,
                    "ticker": rec.ticker,
                    "filings": [],
                },
            )
            entry["filings"].append(
                {
                    "source_type": rec.source_type,
                    "form": rec.form,
                    "doc_period_end": rec.doc_period_end,
                    "filing_date": rec.filing_date,
                    "path": str(rec.path.relative_to(self.repo_root)),
                }
            )
        return sorted(by_cik.values(), key=lambda x: x["ticker"] or x["cik"])

    # ------------------------------------------------------------------
    # XBRL XML Balance Sheet Extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_labels(folder: Path) -> Dict[str, str]:
        """Parse *_lab.xml to map concept names (e.g., us-gaap_Assets) -> human labels."""
        labels: Dict[str, str] = {}
        lab_files = list(folder.glob("*_lab.xml"))
        if not lab_files:
            return labels

        try:
            root = ET.parse(lab_files[0]).getroot()
        except Exception:
            return labels

        xlink_label = "{http://www.w3.org/1999/xlink}label"
        xlink_href = "{http://www.w3.org/1999/xlink}href"
        xlink_from = "{http://www.w3.org/1999/xlink}from"
        xlink_to = "{http://www.w3.org/1999/xlink}to"
        xlink_role = "{http://www.w3.org/1999/xlink}role"

        for link in root.iter():
            if link.tag.split("}")[-1] != "labelLink":
                continue
            locs: Dict[str, str] = {}
            res_labels: Dict[str, Tuple[str, str]] = {}
            arcs: List[Tuple[str, str]] = []
            for child in link:
                tag = child.tag.split("}")[-1]
                if tag == "loc":
                    lbl = child.attrib.get(xlink_label, "")
                    href = child.attrib.get(xlink_href, "")
                    if "#" in href:
                        locs[lbl] = href.split("#")[-1]
                elif tag == "label" and child.text:
                    lbl = child.attrib.get(xlink_label, "")
                    role = child.attrib.get(xlink_role, "")
                    txt = child.text.strip()
                    # Prefer terseLabel or standard label over verbose/documentation
                    is_std = role.endswith("/label") or "terseLabel" in role
                    if lbl not in res_labels or is_std:
                        res_labels[lbl] = (txt, role)
                elif tag == "labelArc":
                    fr = child.attrib.get(xlink_from, "")
                    to = child.attrib.get(xlink_to, "")
                    arcs.append((fr, to))

            for fr, to in arcs:
                concept = locs.get(fr)
                txt_role = res_labels.get(to)
                if concept and txt_role:
                    if concept not in labels or txt_role[1].endswith("/label"):
                        labels[concept] = txt_role[0]
        return labels

    @staticmethod
    def _parse_balance_sheet_cal_children(folder: Path) -> Dict[str, set]:
        """
        Inspect *_cal.xml to determine which concepts roll up into Assets / Liabilities.
        Helps distinguish true 'Total Non-Current Assets' from geographic footnote disclosures.
        """
        children_map: Dict[str, set] = {}
        cal_files = list(folder.glob("*_cal.xml"))
        if not cal_files:
            return children_map

        try:
            root = ET.parse(cal_files[0]).getroot()
        except Exception:
            return children_map

        xlink_label = "{http://www.w3.org/1999/xlink}label"
        xlink_href = "{http://www.w3.org/1999/xlink}href"
        xlink_from = "{http://www.w3.org/1999/xlink}from"
        xlink_to = "{http://www.w3.org/1999/xlink}to"

        for link in root.iter():
            if link.tag.split("}")[-1] != "calculationLink":
                continue
            locs: Dict[str, str] = {}
            for child in link:
                tag = child.tag.split("}")[-1]
                if tag == "loc":
                    lbl = child.attrib.get(xlink_label, "")
                    href = child.attrib.get(xlink_href, "")
                    if "#" in href:
                        locs[lbl] = href.split("#")[-1]
            for child in link:
                if child.tag.split("}")[-1] == "calculationArc":
                    fr = locs.get(child.attrib.get(xlink_from, ""), "")
                    to = locs.get(child.attrib.get(xlink_to, ""), "")
                    if fr and to:
                        children_map.setdefault(fr, set()).add(to)
        return children_map

    def _load_from_xbrl_dir(self, rec: LocalFilingRecord) -> CompanyBalanceSheetDataset:
        folder = rec.path
        inst_path = self._find_instance_xml(folder)
        if inst_path is None:
            raise FileNotFoundError(f"No XBRL instance XML found in {folder}")

        root = ET.parse(inst_path).getroot()
        labels = self._parse_labels(folder)
        cal_children = self._parse_balance_sheet_cal_children(folder)

        # Extract non-dimensional instant contexts
        contexts: Dict[str, str] = {}
        doc_period_end = rec.doc_period_end
        company_name = rec.company_name

        for elem in root:
            local = elem.tag.split("}")[-1]
            if local == "context":
                cid = elem.attrib.get("id")
                has_seg = any(sub.tag.split("}")[-1] == "segment" for sub in elem.iter())
                instant = None
                for sub in elem.iter():
                    if sub.tag.split("}")[-1] == "instant" and sub.text:
                        instant = sub.text.strip()
                        break
                if cid and instant and not has_seg:
                    contexts[cid] = instant

        # Collect numeric facts per instant date
        # facts_by_date[date][local_concept] = (value, decimals, full_tag_key)
        facts_by_date: Dict[str, Dict[str, Tuple[float, Optional[str], str]]] = {}
        for elem in root:
            # Extract namespace URI & local tag
            if "}" in elem.tag:
                ns, local = elem.tag[1:].split("}", 1)
            else:
                ns, local = "", elem.tag

            if local == "DocumentPeriodEndDate" and elem.text and not doc_period_end:
                doc_period_end = elem.text.strip()
            elif local == "EntityRegistrantName" and elem.text and (not company_name or company_name.startswith("CIK ")):
                company_name = elem.text.strip()

            cref = elem.attrib.get("contextRef")
            if cref not in contexts or elem.text is None:
                continue
            txt = elem.text.strip()
            if not txt:
                continue
            try:
                val = float(txt)
            except ValueError:
                continue

            ns_lower = ns.lower()
            if "ifrs" in ns_lower:
                prefix = "ifrs-full"
            elif "us-gaap" in ns_lower:
                prefix = "us-gaap"
            elif "dei" in ns_lower:
                prefix = "dei"
            else:
                prefix = ns.rstrip("/").split("/")[-1]

            dt = contexts[cref]
            full_key = f"{prefix}_{local}" if prefix else local
            # Keep first non-null or exact standard namespace preference
            existing = facts_by_date.setdefault(dt, {}).get(local)
            if existing is None or prefix in ("us-gaap", "ifrs-full"):
                facts_by_date[dt][local] = (val, elem.attrib.get("decimals"), full_key)

        # Find dates that represent balance sheet dates (have Assets or L&E)
        bs_dates = sorted(
            [
                dt
                for dt, fdict in facts_by_date.items()
                if "Assets" in fdict or "LiabilitiesAndStockholdersEquity" in fdict or "EquityAndLiabilities" in fdict
            ],
            reverse=True,
        )

        # Handle 52/53-week fiscal end date split (e.g., Assets on 2026-04-05, Liabilities on 2026-03-31)
        merged_notes_by_date: Dict[str, List[str]] = {}
        if bs_dates:
            primary_dt = doc_period_end if doc_period_end in facts_by_date else bs_dates[0]
            p_facts = facts_by_date[primary_dt]
            has_right_side = any(
                k in p_facts
                for k in ("Liabilities", "LiabilitiesAndStockholdersEquity", "EquityAndLiabilities")
            )
            if "Assets" in p_facts and not has_right_side:
                p_date_obj = datetime.strptime(primary_dt, "%Y-%m-%d").date()
                for other_dt in sorted(facts_by_date.keys(), reverse=True):
                    if other_dt == primary_dt:
                        continue
                    try:
                        o_date_obj = datetime.strptime(other_dt, "%Y-%m-%d").date()
                    except ValueError:
                        continue
                    delta = abs((p_date_obj - o_date_obj).days)
                    o_facts = facts_by_date[other_dt]
                    if 1 <= delta <= 6 and "Assets" not in o_facts and (
                        "LiabilitiesAndStockholdersEquity" in o_facts
                        or "EquityAndLiabilities" in o_facts
                        or "Liabilities" in o_facts
                    ):
                        for k, v in o_facts.items():
                            p_facts.setdefault(k, v)
                        merged_notes_by_date.setdefault(primary_dt, []).append(
                            f"52/53-week context date alignment: merged right-side facts from {other_dt} into {primary_dt} (delta={delta}d)."
                        )
                        if other_dt in bs_dates:
                            bs_dates.remove(other_dt)
                        break

        # Build BalanceSheetPeriodInput for each balance sheet date (keep top 2)
        periods: List[BalanceSheetPeriodInput] = []
        for dt in bs_dates[:2]:
            fdict = facts_by_date[dt]
            periods.append(
                self._facts_dict_to_period_input(
                    period_label=dt,
                    fdict=fdict,
                    labels=labels,
                    cal_children=cal_children,
                    notes=merged_notes_by_date.get(dt, []),
                )
            )

        if not periods:
            raise ValueError(f"No balance sheet dates with Assets found in {folder}")

        meta = self.lookup_company_metadata(rec.cik10)
        return CompanyBalanceSheetDataset(
            cik=rec.cik10,
            cik_int=rec.cik_int,
            company_name=company_name or meta["name"],
            ticker=rec.ticker or meta["ticker"],
            exchange=meta["exchange"],
            sic_description=meta["sic_description"],
            source_type=rec.source_type,
            source_path=str(folder.relative_to(self.repo_root)),
            form=rec.form,
            filing_date=rec.filing_date,
            current_period=periods[0],
            prior_period=periods[1] if len(periods) > 1 else None,
            all_periods=periods,
        )

    @staticmethod
    def _facts_dict_to_period_input(
        period_label: str,
        fdict: Dict[str, Tuple[float, Optional[str], str]],
        labels: Dict[str, str],
        cal_children: Dict[str, set],
        notes: List[str],
    ) -> BalanceSheetPeriodInput:
        def pick(candidates: List[str]) -> Tuple[Optional[float], Optional[str]]:
            for c in candidates:
                if c in fdict:
                    return fdict[c][0], fdict[c][1]
            return None, None

        assets, dec_a = pick(["Assets"])
        liabilities, dec_l = pick(["Liabilities"])
        equity, dec_e = pick(
            [
                "StockholdersEquity",
                "PartnersCapital",
                "MembersEquity",
                "EquityAttributableToOwnersOfParent",
            ]
        )
        equity_with_nci, dec_en = pick(
            [
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                "Equity",
            ]
        )
        nci, _ = pick(["MinorityInterest", "NoncontrollingInterests"])
        mezzanine, _ = pick(
            [
                "RedeemableNoncontrollingInterestEquityCarryingAmount",
                "TemporaryEquityCarryingAmountAttributableToParent",
                "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
                "RedeemablePreferredStockCarryingAmount",
            ]
        )
        liab_eq, _ = pick(["LiabilitiesAndStockholdersEquity", "EquityAndLiabilities"])

        current_assets, _ = pick(["AssetsCurrent", "CurrentAssets"])
        # Non-current assets: avoid US-GAAP segment disclosure tag `us-gaap_NoncurrentAssets`
        noncurrent_assets = None
        if "AssetsNoncurrent" in fdict:
            noncurrent_assets = fdict["AssetsNoncurrent"][0]
        elif "NoncurrentAssets" in fdict and fdict["NoncurrentAssets"][2].startswith("ifrs-full"):
            noncurrent_assets = fdict["NoncurrentAssets"][0]

        # If calculation linkbase exists and Assets has children, verify AssetsNoncurrent is a true subtotal
        assets_cal_kids = (
            cal_children.get("us-gaap_Assets")
            or cal_children.get("ifrs-full_Assets")
            or set()
        )
        if noncurrent_assets is not None and assets_cal_kids:
            # If cal_linkbase defines Assets children and AssetsNoncurrent is NOT one of them, don't treat it as total non-current
            if (
                "us-gaap_AssetsNoncurrent" not in assets_cal_kids
                and "ifrs-full_NoncurrentAssets" not in assets_cal_kids
            ):
                noncurrent_assets = None

        current_liabilities, _ = pick(["LiabilitiesCurrent", "CurrentLiabilities"])
        noncurrent_liabilities, _ = pick(["LiabilitiesNoncurrent", "NoncurrentLiabilities"])

        # Reporting scale from decimals on primary balance sheet lines
        dec_sample = dec_a or dec_l or dec_e or dec_en
        reporting_scale = _decimals_to_scale(dec_sample)

        # Build line_items dictionary (human label -> dollar value) for Step 5 Wrong Row diagnosis
        # Exclude the primary total concepts themselves so a valid metric never self-matches as "wrong_row".
        primary_total_locals = {
            "Assets",
            "Liabilities",
            "StockholdersEquity",
            "PartnersCapital",
            "MembersEquity",
            "EquityAttributableToOwnersOfParent",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "Equity",
            "LiabilitiesAndStockholdersEquity",
            "EquityAndLiabilities",
        }
        line_items: Dict[str, float] = {}
        for local, (val, _, full_key) in fdict.items():
            if local in primary_total_locals:
                continue
            label = labels.get(full_key) or _split_camel_case(local)
            # Avoid duplicate identical key collisions while keeping clean labels
            if label not in line_items:
                line_items[label] = val

        return BalanceSheetPeriodInput(
            period_label=period_label,
            assets=assets,
            liabilities=liabilities,
            equity=equity,
            equity_with_nci=equity_with_nci,
            nci=nci,
            mezzanine_equity=mezzanine,
            liabilities_and_equity=liab_eq,
            current_assets=current_assets,
            noncurrent_assets=noncurrent_assets,
            current_liabilities=current_liabilities,
            noncurrent_liabilities=noncurrent_liabilities,
            line_items=line_items,
            input_scale=1.0,
            reporting_scale=reporting_scale,
            consolidated=True,
            notes=list(notes),
        )

    # ------------------------------------------------------------------
    # SEC CompanyFacts JSON Balance Sheet Extraction (local or live API)
    # ------------------------------------------------------------------
    def _companyfacts_json_to_dataset(
        self,
        data: Dict[str, Any],
        cik10: str,
        cik_int: int,
        source_type: str,
        source_path: str,
        ticker_hint: str = "",
    ) -> CompanyBalanceSheetDataset:
        facts_root = data.get("facts", {})
        # Combine us-gaap and ifrs-full concepts
        concept_records: Dict[str, Dict[str, Any]] = {}
        is_ifrs = "ifrs-full" in facts_root and "us-gaap" not in facts_root
        for tax in ("us-gaap", "ifrs-full"):
            for cname, cbody in facts_root.get(tax, {}).items():
                concept_records[cname] = (tax, cbody)

        # Locate Assets facts to choose latest 10-K (or 10-Q) accession
        if "Assets" not in concept_records:
            raise ValueError(f"No Assets concept found in companyfacts for CIK {cik10}")

        _, assets_body = concept_records["Assets"]
        usd_entries: List[Dict[str, Any]] = []
        for unit_name, entries in assets_body.get("units", {}).items():
            if unit_name.upper() in ("USD", "EUR", "GBP", "CAD"):
                usd_entries.extend(entries)

        periodic = [e for e in usd_entries if e.get("form") in ("10-K", "10-K/A", "10-Q", "10-Q/A", "20-F")]
        if not periodic:
            periodic = usd_entries

        # Prefer latest filing, with 10-K/20-F prioritized if same end date
        periodic.sort(
            key=lambda e: (
                e.get("end", ""),
                1 if e.get("form", "").startswith(("10-K", "20-F")) else 0,
                e.get("filed", ""),
            ),
            reverse=True,
        )
        if not periodic:
            raise ValueError(f"No periodic Assets facts found for CIK {cik10}")

        chosen = periodic[0]
        target_accn = chosen.get("accn")
        target_form = chosen.get("form", "10-K")
        target_filed = chosen.get("filed", "")

        # Gather all instant USD facts belonging to target_accn
        facts_by_date: Dict[str, Dict[str, Tuple[float, Optional[str], str]]] = {}
        labels: Dict[str, str] = {}

        for cname, (tax, cbody) in concept_records.items():
            label_text = cbody.get("label") or _split_camel_case(cname)
            full_key = f"{tax}_{cname}"
            labels[full_key] = label_text
            for unit_name, entries in cbody.get("units", {}).items():
                if unit_name.upper() not in ("USD", "EUR", "GBP", "CAD"):
                    continue
                for entry in entries:
                    if entry.get("accn") != target_accn:
                        continue
                    # Balance sheet facts are instant (no 'start' field)
                    if "start" in entry and entry["start"]:
                        continue
                    dt = entry.get("end")
                    val = entry.get("val")
                    if dt and val is not None:
                        try:
                            fval = float(val)
                        except (ValueError, TypeError):
                            continue
                        facts_by_date.setdefault(dt, {})[cname] = (fval, None, full_key)

        bs_dates = sorted(
            [
                dt
                for dt, fdict in facts_by_date.items()
                if "Assets" in fdict
            ],
            reverse=True,
        )
        if not bs_dates:
            raise ValueError(f"No balance sheet instant dates found in accession {target_accn} for CIK {cik10}")

        periods: List[BalanceSheetPeriodInput] = []
        for dt in bs_dates[:2]:
            fdict = facts_by_date[dt]
            p = self._facts_dict_to_period_input(
                period_label=dt,
                fdict=fdict,
                labels=labels,
                cal_children={},
                notes=["IFRS taxonomy filing." if is_ifrs else "SEC CompanyFacts JSON filing."],
            )
            # Infer reporting scale from trailing zeros of primary balance sheet lines
            sample_vals = [
                v
                for v in (p.assets, p.liabilities, p.equity, p.equity_with_nci, p.liabilities_and_equity)
                if v is not None
            ]
            p.reporting_scale = _infer_scale_from_values(sample_vals)
            periods.append(p)

        meta = self.lookup_company_metadata(cik10)
        return CompanyBalanceSheetDataset(
            cik=cik10,
            cik_int=cik_int,
            company_name=str(data.get("entityName") or meta["name"]),
            ticker=meta["ticker"] or ticker_hint,
            exchange=meta["exchange"],
            sic_description=meta["sic_description"],
            source_type=source_type,
            source_path=source_path,
            form=target_form,
            filing_date=target_filed,
            current_period=periods[0],
            prior_period=periods[1] if len(periods) > 1 else None,
            all_periods=periods,
        )

    def _load_from_entity_json(self, rec: LocalFilingRecord) -> CompanyBalanceSheetDataset:
        data = json.loads(rec.path.read_text(encoding="utf-8"))
        return self._companyfacts_json_to_dataset(
            data=data,
            cik10=rec.cik10,
            cik_int=rec.cik_int,
            source_type=rec.source_type,
            source_path=str(rec.path.relative_to(self.repo_root)),
            ticker_hint=rec.ticker,
        )

    def _fetch_sec_api_companyfacts(self, cik10: str, cik_int: int) -> CompanyBalanceSheetDataset:
        """Optional public SEC EDGAR API fallback when CIK is not in local repo fixtures."""
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
        identity = os.environ.get("EDGAR_IDENTITY", "VAULT Accounting Validator research@example.com")
        req = urllib.request.Request(url, headers={"User-Agent": identity, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return self._companyfacts_json_to_dataset(
            data=data,
            cik10=cik10,
            cik_int=cik_int,
            source_type="sec_api",
            source_path=url,
        )

    # ------------------------------------------------------------------
    # Public Entrypoint: Load by CIK ONLY
    # ------------------------------------------------------------------
    def load_by_cik(
        self,
        cik: Union[int, str],
        prefer_form: Optional[str] = None,
        allow_network: bool = False,
    ) -> CompanyBalanceSheetDataset:
        """
        Fetch balance sheet data for a company given ONLY its Central Index Key (CIK).

        Args:
            cik: Central Index Key (e.g. 320193, '320193', '0000320193').
            prefer_form: Optional form filter ('10-K' or '10-Q'). Defaults to prioritizing
                         the latest 10-K (or latest available filing).
            allow_network: If True and CIK is not found locally in the repo, falls back to
                           SEC EDGAR's public companyfacts API.
        """
        cik10, cik_int = normalize_cik(cik)
        index = self.build_index()
        matches = [r for r in index if r.cik10 == cik10]

        if matches:
            if prefer_form:
                pf = prefer_form.upper()
                filtered = [r for r in matches if r.form.upper().startswith(pf)]
                if filtered:
                    matches = filtered

            # Sort: prefer xbrl_directory over entity json, prefer 10-K, then latest doc_period_end / filing_date
            matches.sort(
                key=lambda r: (
                    1 if r.source_type == "xbrl_directory" else 0,
                    1 if r.form.upper().startswith("10-K") else 0,
                    r.doc_period_end or "",
                    r.filing_date or "",
                ),
                reverse=True,
            )
            chosen = matches[0]
            if chosen.source_type == "xbrl_directory":
                return self._load_from_xbrl_dir(chosen)
            return self._load_from_entity_json(chosen)

        if allow_network:
            return self._fetch_sec_api_companyfacts(cik10, cik_int)

        meta = self.lookup_company_metadata(cik10)
        raise FileNotFoundError(
            f"CIK {cik10} ({meta['name']}) has no local filing fixture in the repository. "
            f"Run with allow_network=True to fetch from SEC EDGAR, or choose one of the "
            f"{len(self.list_available_ciks())} locally cached CIKs."
        )
