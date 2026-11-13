# V.A.U.L.T — System Data Contract & API Specification
**Version: 1.0.0 (MineArena PS-01 Single Source of Truth)**

This document defines the strict data schemas, API routes, pipeline interfaces, and folder boundaries shared across all components of the V.A.U.L.T Human-in-the-Loop Auditor Agent.

---

## 1. Metric Record Schema (JSON)

Every extracted financial metric must adhere to this exact JSON schema:

```json
{
  "metric_id": "run_20261113_001:total_assets",
  "run_id": "run_20261113_001",
  "cik": "0000320193",
  "company": "Apple Inc.",
  "fiscal_year": 2023,
  "metric_name": "total_assets",
  "value": 352583000000.0,
  "unit": "USD",
  "period_start": "2022-10-01",
  "period_end": "2023-09-30",
  "xbrl_tag": "us-gaap:Assets",
  "source_snippet": "Total assets: $352,583 million as of September 30, 2023.",
  "source_url": "https://www.sec.gov/ix?doc=/Archives/edgar/data/0000320193/000032019323000106/aapl-20230930.htm",
  "confidence": 0.98,
  "confidence_breakdown": {
    "grounding": 1.0,
    "label": 1.0,
    "unit": 1.0,
    "yoy": 0.95
  },
  "is_derived": false,
  "flags": [
    {
      "check_name": "accounting_equation",
      "passed": true,
      "severity": "hard",
      "affected_metrics": ["total_assets", "total_liabilities", "shareholders_equity"],
      "message": "Assets (352,583M) == Liabilities (290,437M) + Equity (62,146M)"
    }
  ],
  "status": "AUTO_APPROVED",
  "escalation_reason": null,
  "final_value": 352583000000.0
}
```

### Supported Metric Names
1. `revenue` (Total Net Sales / Operating Revenue)
2. `net_income` (Consolidated Net Income / Loss)
3. `total_assets` (Total Balance Sheet Assets)
4. `total_liabilities` (Total Liabilities; derived as Assets - Equity if unprinted)
5. `shareholders_equity` (Total Stockholders' / Shareholders' Equity)
6. `operating_cash_flow` (Net Cash Provided by Operating Activities)
7. `current_assets` (Total Current Assets)
8. `current_liabilities` (Total Current Liabilities)
9. `cash_and_equivalents` (Cash and Cash Equivalents)
10. `diluted_eps` (Diluted Earnings Per Share)

### Value Scale Convention
All metric `value` entries must be expressed in **full USD units** (e.g. `352583000000.0`, not `352583`). Reporting scale from table headers ("in millions", "in thousands") must be normalized during extraction.

### Status Enumeration
- `AUTO_APPROVED`: Confidence $\ge 0.85$ and no failed checks.
- `NEEDS_REVIEW`: Confidence $< 0.85$ or failed validation check.
- `APPROVED`: Confirmed by human reviewer without changes.
- `CORRECTED`: Modified by human reviewer with an explicit `final_value`.
- `REJECTED`: Discarded by human reviewer as ungrounded or invalid.

---

## 2. Check Result Schema (JSON)

Validation checks executed by the pipeline or agent must output:

```json
{
  "check_name": "accounting_equation",
  "passed": false,
  "severity": "hard",
  "affected_metrics": ["total_assets", "total_liabilities", "shareholders_equity"],
  "message": "Accounting equation mismatch: Assets (352,583M) != Liabilities (290,437M) + Equity (72,146M), diff=-10,000M"
}
```

### Check Names
- `accounting_equation`: Formula checks ($A = L + E$, $AC + ANC = A$, $LC + LNC = L$).
- `segment_reconciliation`: ASC 280 operating segments sum check vs. consolidated total.
- `yoy_change`: Year-over-year percentage movement against configured thresholds.
- `scale_error`: Detection of $\sim 1000\times$ unit/scale mismatch.
- `sign_flip`: Detection of profit-to-loss or loss-to-profit reversals.
- `source_grounding`: Verification that extracted digits appear verbatim in HTML source snippet.

### Severity Levels
- `hard`: Math or accounting equation violation. Forces `NEEDS_REVIEW` on all `affected_metrics` regardless of confidence score (Trigger B).
- `soft`: Outlier or warning (e.g. approaching YoY threshold, minor label ambiguity). Modifies confidence score downwards.

---

## 3. Tamper-Evident Audit Log Schema (JSON)

Logged to `audit_logs/decisions.jsonl` using a cryptographic SHA-256 hash-chain:

```json
{
  "audit_id": "aud_20261113_00042",
  "run_id": "run_20261113_001",
  "metric_id": "run_20261113_001:shareholders_equity",
  "original_value": 72146000000.0,
  "corrected_value": 62146000000.0,
  "decision": "correct",
  "reviewer": "Alex Morgan (CPA)",
  "comment": "Corrected typo in extracted equity from 72,146M to 62,146M verified against Page 64 Table 2.",
  "timestamp_utc": "2026-11-13T02:30:00Z",
  "prev_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "hash": "4a5b6c7d8e9f0123456789abcdef0123456789abcdef0123456789abcdef0123"
}
```

- `hash` calculation: $\text{SHA-256}(\text{prev\_hash} + \text{audit\_id} + \text{run\_id} + \text{metric\_id} + \text{decision} + \text{str(corrected\_value)} + \text{reviewer} + \text{timestamp\_utc})$.
- The genesis line uses `prev_hash: "0000000000000000000000000000000000000000000000000000000000000000"`.

---

## 4. Backend REST API Endpoints

Base path: `/api/v1`

| Method | Endpoint | Description | Request Body | Response Body |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/runs` | Start extraction & audit run for a CIK | `{"cik": "0000320193", "fiscal_year": 2023, "inject_error": null}` | `{"run_id": "run_123", "status": "RUNNING"}` |
| `GET` | `/runs` | List all historical and active runs | None | `[{"run_id": "run_123", "company": "Apple", "status": "..."}]` |
| `GET` | `/runs/{run_id}` | Detailed state of a run | None | Full run payload: status, metrics, checks, counters |
| `GET` | `/reviews` | Get metrics needing review | Query: `?status=pending\|completed` | List of Metric Records |
| `POST` | `/runs/{run_id}/review` | Submit human review decision | `{"metric_id": "...", "decision": "approve\|correct\|reject", "corrected_value": 1.0, "reviewer": "Name", "comment": "..."}` | `{"status": "ok", "remaining_reviews": 0, "run_status": "COMPLETED"}` |
| `GET` | `/runs/{run_id}/accuracy-report` | Compare extractions against SEC Company Facts | None | Accuracy Report JSON |
| `GET` | `/audit-log` | Get all decision lines | None | List of Audit Log Lines |
| `GET` | `/audit-log/verify` | Verify cryptographic hash chain | None | `{"verified": true, "total_records": 42, "broken_index": null}` |
| `GET` | `/config/thresholds` | Read current thresholds | None | Thresholds YAML as JSON |
| `PUT` | `/config/thresholds` | Update thresholds | `{"confidence_threshold": 0.85, ...}` | `{"status": "updated", "config": {...}}` |

---

## 5. Pipeline Function Interfaces (`pipeline/`)

Member A must implement these six functions in `pipeline/`:

1. `fetch_filing(cik: str, fiscal_year: int, *, allow_network: bool = True) -> dict`
   - **Input**: 10-digit CIK string and integer fiscal year.
   - **Output**: Dictionary containing filing metadata, HTML contents, and XBRL instance directory path.
2. `extract_metrics(filing: dict) -> list[dict]`
   - **Input**: Filing dictionary from `fetch_filing`.
   - **Output**: List of 10 Metric Record dictionaries with raw values, units, periods, and source HTML snippets.
3. `score_metrics(records: list[dict], *, config: dict | None = None) -> list[dict]`
   - **Input**: List of Metric Record dictionaries.
   - **Output**: Same list with `confidence` and `confidence_breakdown` calculated via 4-factor formula.
4. `run_validations(records: list[dict], history: list[dict] | None = None, *, config: dict | None = None) -> tuple[list[dict], list[dict]]`
   - **Input**: Scored metric records and optional prior-year metric records.
   - **Output**: `(updated_records, check_results)` with `status` updated to `AUTO_APPROVED` or `NEEDS_REVIEW`.
5. `fetch_ground_truth(cik: str, fiscal_year: int) -> dict[str, float]`
   - **Input**: CIK and fiscal year.
   - **Output**: Official reported values from SEC Company Facts API indexed by standard metric name.
6. `build_accuracy_report(records: list[dict], ground_truth: dict[str, float]) -> dict`
   - **Input**: Processed records and ground truth dictionary.
   - **Output**: Dictionary with overall accuracy, escalation precision, breakdown per metric, and missed errors list.

---

## 6. Folder Ownership & Team Boundaries

| Member | Focus Area | Owned Folders / Files | Responsibilities |
| :--- | :--- | :--- | :--- |
| **A** | Data Pipeline | `pipeline/` | CIK fetch, 10 metrics extraction, scoring, validation wrappers, ground truth comparison |
| **B** | Agent Brain | `agent/`, `app/api/runs_routes.py`, `config/` | LangGraph workflow, pause/resume, SQLite checkpoints, hash-chained audit log, REST routes |
| **C** | Face & Proof | `dashboard/`, `scripts/`, `README.md`, `docs/` | Streamlit UI (Review Queue, Overview, Accuracy, Audit), smoke tests, demo script |

*Existing engines ([segmentation/](file:///e:/VAULT/segmentation), [YOY_model/](file:///e:/VAULT/YOY_model), [validation/](file:///e:/VAULT/validation), [data_verify/](file:///e:/VAULT/data_verify)) must be wrapped by `pipeline/` and never deleted or rewritten.*

---

## 7. Global Configuration (`config/thresholds.yaml`)

```yaml
confidence_threshold: 0.85
auto_approve_enabled: true
default_yoy_pct: 40.0
warn_ratio: 0.75

confidence_weights:
  grounding: 0.40
  label: 0.25
  unit: 0.20
  yoy: 0.15

metrics_yoy_thresholds:
  revenue: 50.0
  net_income: 150.0
  total_assets: 30.0
  total_liabilities: 40.0
  shareholders_equity: 40.0
  operating_cash_flow: 60.0
  current_assets: 35.0
  current_liabilities: 35.0
  cash_and_equivalents: 75.0
  diluted_eps: 50.0
```

