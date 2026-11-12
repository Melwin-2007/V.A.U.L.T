"""Thin repository adapter loading already-extracted balance sheet metrics.

IMPORTANT (Section 0 Rule):
- NEVER reads SEC XBRL Company Facts (`companyfacts` JSON API or `entity/*_facts.json`).
- Reads strictly from filing-level XBRL instance/linkbase files in the repository
  (`edgartools/tests/fixtures/xbrl/`), using pure Python XML/JSON parsing so it works
  with or without optional third-party `edgartools` network/XML dependencies installed.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import unquote

from .models import BalanceSheetInput, BalanceSheetPeriodInput, to_decimal

DEFAULT_FIXTURES_ROOT = Path(__file__).resolve().parent.parent / "edgartools" / "tests" / "fixtures" / "xbrl"
DEFAULT_TICKERS_PARQUET = (
    Path(__file__).resolve().parent.parent / "edgartools" / "edgar" / "reference" / "data" / "company_tickers.parquet"
)

MISSING_REPO_FIELDS: List[str] = [
    "base_confidence (repo extractor Fact/Statement emits no per-metric confidence score; engine uses config default)",
    "alt_candidates (repo extractor selects a single fact per concept/period rather than alternative candidates)",
    "column_index (XBRL XML Fact model does not preserve HTML visual table column index)",
    "source_snippet (XBRL instance XML carries raw numeric value and unitRef, not rendered HTML table snippet)",
]

XBRLI_NS = "{http://www.xbrl.org/2003/instance}"
LINK_NS = "{http://www.xbrl.org/2003/linkbase}"
XLINK_NS = "{http://www.w3.org/1999/xlink}"
XSI_NS = "{http://www.w3.org/2001/XMLSchema-instance}"

# Canonical US-GAAP concept local-name mappings (strictly consolidated balance sheet).
# Note: us-gaap:NoncurrentAssets is a geographic disclosure (e.g. Microsoft) and MUST NOT map to ANC.
PRIMARY_TOTAL_CONCEPTS: Set[str] = {
    "Assets",
    "Liabilities",
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "PartnersCapital",
    "PartnersCapitalIncludingPortionAttributableToNoncontrollingInterest",
    "LiabilitiesAndStockholdersEquity",
    "LiabilitiesAndPartnersCapital",
}

CONCEPT_ROLE_MAP: Dict[str, Tuple[str, ...]] = {
    "A": ("Assets",),
    "L": ("Liabilities",),
    "E_PARENT": ("StockholdersEquity", "PartnersCapital"),
    "E_TOTAL": (
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "PartnersCapitalIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "NCI": (
        "MinorityInterest",
        "NoncontrollingInterest",
        "LimitedPartnersCapitalAccount",
    ),
    "TE": (
        "TemporaryEquityCarryingAmountAttributableToParent",
        "RedeemableNoncontrollingInterestEquityCarryingAmount",
        "RedeemableNoncontrollingInterestEquityPreferredCarryingAmount",
        "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
    ),
    "LE": ("LiabilitiesAndStockholdersEquity", "LiabilitiesAndPartnersCapital"),
    "AC": ("AssetsCurrent",),
    "ANC": ("AssetsNoncurrent",),
    "LC": ("LiabilitiesCurrent",),
    "LNC": ("LiabilitiesNoncurrent",),
}


@dataclass
class _FilingEntry:
    instance_path: Path
    directory: Path
    cik: str
    company: str
    form: Optional[str]
    accession: Optional[str]
    period_end_hint: Optional[str]
    fiscal_year_hint: Optional[int]
    ticker: Optional[str]


def normalize_cik(cik: Any) -> str:
    digits = re.sub(r"\D", "", str(cik or ""))
    if not digits:
        raise ValueError(f"Invalid CIK: {cik!r}")
    return digits.zfill(10)


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _prefix_from_tag(tag: str) -> str:
    if "}" in tag:
        ns = tag[1:].split("}", 1)[0]
        return ns.rstrip("/").rsplit("/", 1)[-1]
    return ""


def _parse_iso_date(text: Optional[str]) -> Optional[date]:
    if not text:
        return None
    try:
        return date.fromisoformat(text.strip()[:10])
    except ValueError:
        return None


def _decimals_to_scale(decimals_str: Optional[str]) -> Optional[int]:
    if not decimals_str:
        return None
    s = decimals_str.strip().upper()
    if s in ("INF", "0", "+0", "-0"):
        return 1
    try:
        dec = int(s)
    except ValueError:
        return None
    if dec <= -9:
        return 1_000_000_000
    if dec <= -6:
        return 1_000_000
    if dec <= -3:
        return 1_000
    return 1


def _lookup_company_name_from_reference(cik_digits: str) -> Optional[str]:
    """Optional name-only lookup from static company_tickers.parquet (never Company Facts)."""
    if not DEFAULT_TICKERS_PARQUET.exists():
        return None
    try:
        import pyarrow.parquet as pq  # type: ignore

        table = pq.read_table(DEFAULT_TICKERS_PARQUET, columns=["cik", "name"])
        target_int = int(cik_digits)
        ciks = table["cik"].to_pylist()
        names = table["name"].to_pylist()
        for c, n in zip(ciks, names):
            if c == target_int and n:
                return str(n)
    except Exception:
        return None
    return None


def _scan_dei_header(instance_path: Path) -> Dict[str, Optional[str]]:
    """Read only DEI entity tags from an XBRL instance XML file."""
    info: Dict[str, Optional[str]] = {
        "cik": None,
        "company": None,
        "form": None,
        "period_end": None,
        "fiscal_year": None,
        "ticker": None,
    }
    wanted = {
        "EntityCentralIndexKey": "cik",
        "EntityRegistrantName": "company",
        "DocumentType": "form",
        "DocumentPeriodEndDate": "period_end",
        "DocumentFiscalYearFocus": "fiscal_year",
        "TradingSymbol": "ticker",
    }
    for event, elem in ET.iterparse(instance_path, events=("end",)):
        local = _strip_ns(elem.tag)
        if local in wanted and "dei" in elem.tag.lower():
            val = (elem.text or "").strip()
            key = wanted[local]
            if val and not info[key]:
                info[key] = val
        if all(info[k] is not None for k in ("cik", "company", "form", "period_end")):
            elem.clear()
            break
        elem.clear()
    return info


def _find_instance_files(root: Path) -> List[Path]:
    files = sorted(root.rglob("*_htm.xml"))
    if not files:
        files = sorted(p for p in root.rglob("*.xml") if not p.name.endswith(("_pre.xml", "_cal.xml", "_lab.xml", "_def.xml", ".xsd.xml")))
    return files


def _build_filing_index(fixtures_root: Path) -> List[_FilingEntry]:
    entries: List[_FilingEntry] = []
    for instance_path in _find_instance_files(fixtures_root):
        directory = instance_path.parent
        meta: Dict[str, Any] = {}
        for name in ("metadata.json", "manifest.json"):
            mpath = directory / name
            if mpath.exists():
                try:
                    meta = json.loads(mpath.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
                break
        cik_raw = meta.get("cik")
        company = meta.get("company")
        form = meta.get("form")
        accession = meta.get("accession_no") or meta.get("accession")
        ticker = meta.get("ticker")
        period_end_hint = None
        fiscal_year_hint = None

        if not cik_raw or not company or not form:
            dei = _scan_dei_header(instance_path)
            cik_raw = cik_raw or dei.get("cik")
            company = company or dei.get("company")
            form = form or dei.get("form")
            period_end_hint = dei.get("period_end")
            ticker = ticker or dei.get("ticker")
            if dei.get("fiscal_year") and dei["fiscal_year"].isdigit():
                fiscal_year_hint = int(dei["fiscal_year"])
        else:
            # Quick check for DEI period_end if folder name doesn't expose year
            m_year = re.search(r"(\d{4})", instance_path.name)
            if m_year:
                yr = int(m_year.group(1))
                if 1990 <= yr <= 2100:
                    fiscal_year_hint = yr

        if not cik_raw:
            continue
        try:
            cik_norm = normalize_cik(cik_raw)
        except ValueError:
            continue

        if period_end_hint and not fiscal_year_hint:
            dt = _parse_iso_date(period_end_hint)
            if dt:
                fiscal_year_hint = dt.year
        m_dir_yr = re.search(r"(20\d{2}|19\d{2})", directory.name)
        dir_year = int(m_dir_yr.group(1)) if m_dir_yr else None

        entries.append(
            _FilingEntry(
                instance_path=instance_path,
                directory=directory,
                cik=cik_norm,
                company=str(company or _lookup_company_name_from_reference(cik_norm) or cik_norm),
                form=str(form) if form else None,
                accession=str(accession) if accession else None,
                period_end_hint=period_end_hint,
                fiscal_year_hint=fiscal_year_hint or dir_year,
                ticker=str(ticker) if ticker else None,
            )
        )
    return entries


def _select_filing(
    entries: List[_FilingEntry],
    cik: str,
    fiscal_year: Optional[int],
    accession: Optional[str],
) -> _FilingEntry:
    cik_norm = normalize_cik(cik)
    matches = [e for e in entries if e.cik == cik_norm]
    if not matches:
        raise FileNotFoundError(
            f"No non-CompanyFacts filing fixture found in repo for CIK {cik_norm}."
        )
    if accession:
        acc_clean = accession.strip()
        acc_matches = [e for e in matches if e.accession and e.accession.strip() == acc_clean]
        if acc_matches:
            matches = acc_matches
        else:
            raise FileNotFoundError(
                f"No filing fixture found for CIK {cik_norm} with accession {acc_clean}."
            )

    def _score(entry: _FilingEntry) -> Tuple[int, int, int, str]:
        is_10k = 1 if (entry.form or "").upper().startswith("10-K") else 0
        year_match = 0
        if fiscal_year is not None:
            if entry.fiscal_year_hint == fiscal_year:
                year_match = 3
            elif entry.period_end_hint and entry.period_end_hint.startswith(str(fiscal_year)):
                year_match = 2
            elif str(fiscal_year) in entry.directory.as_posix():
                year_match = 1
        yr_val = entry.fiscal_year_hint or 0
        # Prefer exact year match, then 10-K over 10-Q, then later year
        return (year_match, is_10k, yr_val, entry.instance_path.name)

    best = max(matches, key=_score)
    if fiscal_year is not None and _score(best)[0] == 0:
        # Allow if the balance sheet itself covers the requested year (checked after parse? No, check DEI/folder)
        # Keep available matches fallback sorted if requested year matches prior year of a 10-K
        matches.sort(key=lambda e: (1 if (e.form or "").upper().startswith("10-K") else 0, e.fiscal_year_hint or 0), reverse=True)
        return matches[0]
    return best


def _load_labels(directory: Path) -> Dict[str, str]:
    """Load human-readable labels keyed by concept local name from *_lab.xml."""
    labels: Dict[str, str] = {}
    lab_files = sorted(directory.glob("*_lab.xml"))
    if not lab_files:
        return labels
    try:
        tree = ET.parse(lab_files[0])
        root = tree.getroot()
    except Exception:
        return labels

    loc_concepts: Dict[str, str] = {}
    label_texts: Dict[str, Tuple[str, str]] = {}
    for elem in root.iter():
        tag = _strip_ns(elem.tag)
        if tag == "loc":
            xlink_label = elem.attrib.get(f"{XLINK_NS}label")
            href = elem.attrib.get(f"{XLINK_NS}href", "")
            if xlink_label and "#" in href:
                frag = unquote(href.split("#", 1)[1])
                local = frag.split("_", 1)[-1] if "_" in frag else frag
                loc_concepts[xlink_label] = local
        elif tag == "label":
            xlink_label = elem.attrib.get(f"{XLINK_NS}label")
            role = elem.attrib.get(f"{XLINK_NS}role", "")
            txt = (elem.text or "").strip()
            if xlink_label and txt:
                # Prefer terseLabel / label / totalLabel cleanly
                prev = label_texts.get(xlink_label)
                if prev is None or role.endswith("/label"):
                    label_texts[xlink_label] = (role, txt)

    arcs: List[Tuple[str, str]] = []
    for elem in root.iter():
        if _strip_ns(elem.tag) == "labelArc":
            f_lbl = elem.attrib.get(f"{XLINK_NS}from")
            t_lbl = elem.attrib.get(f"{XLINK_NS}to")
            if f_lbl and t_lbl:
                arcs.append((f_lbl, t_lbl))

    for f_lbl, t_lbl in arcs:
        concept = loc_concepts.get(f_lbl)
        txt_info = label_texts.get(t_lbl)
        if concept and txt_info and concept not in labels:
            labels[concept] = txt_info[1]
    return labels


def _load_balance_sheet_concepts(directory: Path) -> Tuple[Set[str], Optional[str], bool]:
    """Identify concepts belonging to the Consolidated Balance Sheet from *_pre.xml / *_cal.xml."""
    bs_concepts: Set[str] = set()
    role_title: Optional[str] = None
    is_consolidated = True

    def _is_bs_role(uri: str, title: str = "") -> bool:
        hay = f"{uri} {title}".lower()
        if "parenthetical" in hay or "disclosure" in hay or "schedule" in hay:
            return False
        return any(
            k in hay
            for k in (
                "balancesheet",
                "balance_sheet",
                "balance sheet",
                "financialposition",
                "financial_position",
                "financial position",
                "statementoffinancialcondition",
                "financial condition",
            )
        )

    schema_roles: Dict[str, str] = {}
    for xsd_path in sorted(directory.glob("*.xsd*")):
        try:
            tree = ET.parse(xsd_path)
            for elem in tree.getroot().iter():
                if _strip_ns(elem.tag) == "roleType":
                    uri = elem.attrib.get("roleURI", "")
                    defn_el = next((c for c in elem if _strip_ns(c.tag) == "definition"), None)
                    if uri and defn_el is not None and defn_el.text:
                        schema_roles[uri] = defn_el.text.strip()
        except Exception:
            continue

    for suffix in ("*_pre.xml", "*_cal.xml"):
        for lb_path in sorted(directory.glob(suffix)):
            try:
                tree = ET.parse(lb_path)
            except Exception:
                continue
            for link in tree.getroot():
                tag = _strip_ns(link.tag)
                if tag not in ("presentationLink", "calculationLink"):
                    continue
                role_uri = link.attrib.get(f"{XLINK_NS}role", "")
                title = schema_roles.get(role_uri, "")
                if _is_bs_role(role_uri, title):
                    if role_title is None:
                        role_title = title or role_uri.rsplit("/", 1)[-1]
                    if "parent only" in f"{role_uri} {title}".lower() and "consolidated" not in f"{role_uri} {title}".lower():
                        is_consolidated = False
                    for child in link:
                        if _strip_ns(child.tag) == "loc":
                            href = child.attrib.get(f"{XLINK_NS}href", "")
                            if "#" in href:
                                frag = unquote(href.split("#", 1)[1])
                                local = frag.split("_", 1)[-1] if "_" in frag else frag
                                bs_concepts.add(local)

    return bs_concepts, role_title, is_consolidated


def _parse_xbrl_balance_sheet(entry: _FilingEntry, requested_year: Optional[int]) -> BalanceSheetInput:
    """Parse XBRL instance XML + linkbases into BalanceSheetInput."""
    labels = _load_labels(entry.directory)
    bs_concepts, role_title, is_consolidated = _load_balance_sheet_concepts(entry.directory)

    tree = ET.parse(entry.instance_path)
    root = tree.getroot()

    # Context ID -> instant date (strictly non-dimensional consolidated contexts)
    contexts: Dict[str, str] = {}
    dei: Dict[str, str] = {}
    raw_facts: List[Tuple[str, str, str, str, Optional[str], Decimal]] = []  # prefix, local, ctx, unit, decimals, val

    for child in root:
        tag_local = _strip_ns(child.tag)
        if tag_local == "context":
            ctx_id = child.attrib.get("id")
            if not ctx_id:
                continue
            has_dimension = False
            instant_date: Optional[str] = None
            for sub in child.iter():
                sub_local = _strip_ns(sub.tag)
                if sub_local in ("segment", "scenario") and len(list(sub)) > 0:
                    has_dimension = True
                    break
                if sub_local == "instant" and sub.text:
                    instant_date = sub.text.strip()[:10]
            if not has_dimension and instant_date:
                contexts[ctx_id] = instant_date
        elif tag_local in ("unit", "schemaRef", "roleRef", "arcroleRef", "linkbaseRef", "footnoteLink"):
            continue
        else:
            prefix = _prefix_from_tag(child.tag)
            if prefix == "dei":
                val_txt = (child.text or "").strip()
                if val_txt and tag_local not in dei:
                    dei[tag_local] = val_txt
                continue
            if child.attrib.get(f"{XSI_NS}nil") == "true":
                continue
            ctx_ref = child.attrib.get("contextRef")
            unit_ref = child.attrib.get("unitRef", "")
            decimals_attr = child.attrib.get("decimals")
            if not ctx_ref or ctx_ref not in contexts:
                continue
            # Monetary facts only (unitRef exists; skip pure/shares when clear)
            if not unit_ref or "share" in unit_ref.lower():
                continue
            val = to_decimal(child.text)
            if val is None:
                continue
            raw_facts.append((prefix, tag_local, ctx_ref, unit_ref, decimals_attr, val))

    doc_period_end = dei.get("DocumentPeriodEndDate") or entry.period_end_hint
    company = entry.company or dei.get("EntityRegistrantName") or entry.cik
    fiscal_year = entry.fiscal_year_hint
    if dei.get("DocumentFiscalYearFocus", "").isdigit():
        fiscal_year = int(dei["DocumentFiscalYearFocus"])

    # Organize instant facts by date -> concept local -> list[(decimals, val, concept_key)]
    by_date: Dict[str, Dict[str, List[Tuple[Optional[str], Decimal, str]]]] = defaultdict(lambda: defaultdict(list))
    for prefix, local, ctx_ref, unit_ref, decimals_attr, val in raw_facts:
        dt = contexts[ctx_ref]
        by_date[dt][local].append((decimals_attr, val, f"{prefix}:{local}" if prefix else local))

    # Candidate dates that report Assets or LiabilitiesAndStockholdersEquity or StockholdersEquity
    key_bs_concepts = {"Assets", "Liabilities", "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "LiabilitiesAndStockholdersEquity"}
    bs_dates = sorted(
        [
            dt
            for dt, cmap in by_date.items()
            if len(key_bs_concepts & set(cmap.keys())) >= 2
        ],
        reverse=True,
    )
    if not bs_dates:
        # Fallback if terms sit on nearby 52/53-week dates (e.g. npk/10q_2026q1)
        bs_dates = sorted(
            [dt for dt, cmap in by_date.items() if bool(key_bs_concepts & set(cmap.keys()))],
            reverse=True,
        )

    if not bs_dates:
        raise ValueError(f"No balance sheet instant facts found in {entry.instance_path}")

    # Pick current date: match DocumentPeriodEndDate if present, or latest date matching requested_year
    current_date = bs_dates[0]
    if requested_year is not None:
        yr_matches = [d for d in bs_dates if d.startswith(str(requested_year))]
        if yr_matches:
            current_date = yr_matches[0]
    elif doc_period_end and doc_period_end in bs_dates:
        current_date = doc_period_end

    # Pick prior date: comparative period ~1 year before current_date (250 to 430 days)
    cur_dt_obj = _parse_iso_date(current_date)
    prior_date: Optional[str] = None
    if cur_dt_obj:
        best_delta = 9999
        for cand in bs_dates:
            if cand >= current_date:
                continue
            cand_obj = _parse_iso_date(cand)
            if not cand_obj:
                continue
            days = (cur_dt_obj - cand_obj).days
            if 250 <= days <= 430 and abs(days - 365) < best_delta:
                best_delta = abs(days - 365)
                prior_date = cand
        if prior_date is None:
            earlier = [d for d in bs_dates if d < current_date]
            if earlier:
                prior_date = earlier[0]

    # Collect per-period mismatched terms (e.g. npk has Assets on 2026-04-05 and Liabilities on 2026-03-31 within +/- 10 days)
    def _build_period(target_date: str) -> BalanceSheetPeriodInput:
        cmap = by_date.get(target_date, {})
        metric_periods: Dict[str, str] = {}
        target_dt = _parse_iso_date(target_date)

        def _pick_concept(candidates: Tuple[str, ...]) -> Tuple[Optional[Decimal], Optional[str], Optional[str], Optional[str]]:
            for cname in candidates:
                hits = cmap.get(cname)
                if hits:
                    # Prefer first non-nil value; in US GAAP Assets/Liabilities/Equity appear once per non-dim instant context
                    dec_attr, val, full_concept = hits[0]
                    return val, dec_attr, full_concept, target_date
            # Check nearby +/- 10 days for period mismatch detection (do NOT silently pretend same date!)
            if target_dt:
                for other_date, other_cmap in by_date.items():
                    if other_date == target_date:
                        continue
                    odt = _parse_iso_date(other_date)
                    if odt and 0 < abs((target_dt - odt).days) <= 10:
                        for cname in candidates:
                            hits = other_cmap.get(cname)
                            if hits:
                                dec_attr, val, full_concept = hits[0]
                                return val, dec_attr, full_concept, other_date
            return None, None, None, None

        extracted: Dict[str, Optional[Decimal]] = {}
        decimals_seen: List[int] = []
        snippets: Dict[str, str] = {}

        def _assign(slot: str, metric_key: str, concepts: Tuple[str, ...]) -> None:
            val, dec_attr, full_c, actual_dt = _pick_concept(concepts)
            extracted[slot] = val
            if dec_attr is not None:
                sc = _decimals_to_scale(dec_attr)
                if sc:
                    decimals_seen.append(sc)
            if full_c:
                snippets[metric_key] = full_c
            if val is not None and actual_dt:
                metric_periods[metric_key] = actual_dt

        _assign("A", "total_assets", CONCEPT_ROLE_MAP["A"])
        _assign("L", "total_liabilities", CONCEPT_ROLE_MAP["L"])
        _assign("LE", "total_liabilities_and_equity", CONCEPT_ROLE_MAP["LE"])
        _assign("AC", "current_assets", CONCEPT_ROLE_MAP["AC"])
        _assign("ANC", "noncurrent_assets", CONCEPT_ROLE_MAP["ANC"])
        _assign("LC", "current_liabilities", CONCEPT_ROLE_MAP["LC"])
        _assign("LNC", "noncurrent_liabilities", CONCEPT_ROLE_MAP["LNC"])

        # Section 3 Rule 6: parent-only Equity vs total Equity including NCI
        e_parent, e_dec, e_c, e_dt = _pick_concept(CONCEPT_ROLE_MAP["E_PARENT"])
        e_total_inc, et_dec, et_c, et_dt = _pick_concept(CONCEPT_ROLE_MAP["E_TOTAL"])
        nci_val, nci_dec, nci_c, nci_dt = _pick_concept(CONCEPT_ROLE_MAP["NCI"])

        e_includes_nci = False
        equity_basis = "parent_only"
        if e_parent is not None:
            extracted["E"] = e_parent
            if e_dt:
                metric_periods["equity"] = e_dt
            if e_c:
                snippets["equity"] = e_c
            if e_dec and _decimals_to_scale(e_dec):
                decimals_seen.append(_decimals_to_scale(e_dec))  # type: ignore[arg-type]
            extracted["NCI"] = nci_val
            if nci_val is not None:
                if nci_dt:
                    metric_periods["noncontrolling_interest"] = nci_dt
                if nci_c:
                    snippets["noncontrolling_interest"] = nci_c
        elif e_total_inc is not None:
            extracted["E"] = e_total_inc
            e_includes_nci = True
            equity_basis = "total_including_nci"
            if et_dt:
                metric_periods["equity"] = et_dt
            if et_c:
                snippets["equity"] = et_c
            if et_dec and _decimals_to_scale(et_dec):
                decimals_seen.append(_decimals_to_scale(et_dec))  # type: ignore[arg-type]
            # Per Section 3 Rule 6: record E_total and do not double-count NCI
            extracted["NCI"] = Decimal(0)
        else:
            extracted["E"] = None
            extracted["NCI"] = nci_val

        # Mezzanine / temporary equity (deduplicate aliases so TSLA is not double-counted)
        te_val, te_dec, te_c, te_dt = _pick_concept(CONCEPT_ROLE_MAP["TE"])
        extracted["TE"] = te_val
        if te_val is not None:
            if te_dt:
                metric_periods["temporary_equity"] = te_dt
            if te_c:
                snippets["temporary_equity"] = te_c
            if te_dec and _decimals_to_scale(te_dec):
                decimals_seen.append(_decimals_to_scale(te_dec))  # type: ignore[arg-type]

        # Per-table dominant scale from balance-sheet monetary decimals
        if decimals_seen:
            scale = Counter(decimals_seen).most_common(1)[0][0]
        else:
            # Fallback across all monetary facts on target_date
            fallback_scales = [
                _decimals_to_scale(dec_attr)
                for hits in cmap.values()
                for dec_attr, _, _ in hits
                if _decimals_to_scale(dec_attr)
            ]
            scale = Counter(fallback_scales).most_common(1)[0][0] if fallback_scales else 1_000_000

        # Build parsed `rows` {label: Decimal_dollars} for "wrong_row" diagnosis.
        # Exclude primary grand totals so metrics never self-match, while keeping subtotals (AC/LC/LNC/ANC) and lines.
        excluded_concepts = PRIMARY_TOTAL_CONCEPTS | {
            "MinorityInterest",
            "NoncontrollingInterest",
            "TemporaryEquityCarryingAmountAttributableToParent",
            "RedeemableNoncontrollingInterestEquityCarryingAmount",
            "TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests",
        }
        rows: Dict[str, Decimal] = {}
        candidate_concepts = bs_concepts if bs_concepts else set(cmap.keys())
        for cname in sorted(candidate_concepts):
            if cname in excluded_concepts or cname not in cmap:
                continue
            _, rval, _ = cmap[cname][0]
            rlabel = labels.get(cname) or cname
            if rlabel not in rows:
                rows[rlabel] = rval

        return BalanceSheetPeriodInput(
            period_end=target_date,
            A=extracted.get("A"),
            L=extracted.get("L"),
            E=extracted.get("E"),
            NCI=extracted.get("NCI"),
            TE=extracted.get("TE"),
            LE=extracted.get("LE"),
            AC=extracted.get("AC"),
            ANC=extracted.get("ANC"),
            LC=extracted.get("LC"),
            LNC=extracted.get("LNC"),
            scale=scale,
            rows=rows,
            metric_periods=metric_periods,
            metric_snippets=snippets,
            e_includes_nci=e_includes_nci,
            equity_reporting_basis=equity_basis,
            is_consolidated=is_consolidated,
            already_normalized=True,  # XBRL instance facts are already in plain dollars
            statement_title=role_title,
        )

    current_period = _build_period(current_date)
    prior_period = _build_period(prior_date) if prior_date else None

    if prior_period is not None:
        current_period.prior_values = {
            "total_assets": prior_period.A,
            "total_liabilities": prior_period.L,
            "equity": prior_period.E,
            "noncontrolling_interest": prior_period.NCI,
            "temporary_equity": prior_period.TE,
            "total_liabilities_and_equity": prior_period.LE,
        }

    return BalanceSheetInput(
        cik=entry.cik,
        company=company,
        accession=entry.accession,
        fiscal_year=fiscal_year,
        form=entry.form,
        current=current_period,
        prior=prior_period,
        missing_extractor_fields=list(MISSING_REPO_FIELDS),
        source_path=str(entry.instance_path),
    )


def list_available_filings(fixtures_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return catalog of available non-CompanyFacts filings in the repo."""
    root = fixtures_root or DEFAULT_FIXTURES_ROOT
    return [
        {
            "cik": e.cik,
            "company": e.company,
            "ticker": e.ticker,
            "form": e.form,
            "fiscal_year": e.fiscal_year_hint,
            "accession": e.accession,
            "period_end": e.period_end_hint,
            "path": str(e.instance_path),
        }
        for e in _build_filing_index(root)
    ]


def load_balance_sheet(
    cik: str,
    fiscal_year: Optional[int] = None,
    accession: Optional[str] = None,
    fixtures_root: Optional[Path] = None,
) -> BalanceSheetInput:
    """Thin adapter entry point: load already-extracted balance sheet metrics for a filing.

    All repo-specific file discovery and XML/linkbase extraction lives here.
    Does NOT use SEC XBRL Company Facts.
    """
    root = fixtures_root or DEFAULT_FIXTURES_ROOT
    entries = _build_filing_index(root)
    chosen = _select_filing(entries, cik=cik, fiscal_year=fiscal_year, accession=accession)
    return _parse_xbrl_balance_sheet(chosen, requested_year=fiscal_year)

