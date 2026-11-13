# V.A.U.L.T (Verifiable Auditing & Unsupervised Ledger Testing)
**Comprehensive Project State & Architecture Specification**

---

## 1. Executive Summary

**V.A.U.L.T** is a deterministic, audit-grade verification and validation platform for SEC Form 10-K and 10-Q financial data. It eliminates reliance on ungrounded Large Language Model (LLM) assertions by applying exact mathematical proofs, accounting identity checks, source text grounding, and statistical outlier models to SEC filings.

The platform processes filings either directly via Central Index Key (CIK) codes from SEC EDGAR or offline against local XBRL and Parquet filing archives.

---

## 2. Core Architecture & Component Map

The platform is structured into five cohesive subsystems:

```
                          ┌──────────────────────────────────────────────┐
                          │         SEC EDGAR / Local XBRL Data          │
                          │   (edgartools/ & local filing fixtures)      │
                          └──────────────────────┬───────────────────────┘
                                                 │
                   ┌─────────────────────────────┼─────────────────────────────┐
                   ▼                             ▼                             ▼
       ┌───────────────────────┐   ┌───────────────────────────┐   ┌───────────────────────┐
       │   segmentation/       │   │       YOY_model/          │   │      validation/      │
       │ ASC 280 Segments      │   │ Year-on-Year Consistency  │   │ 7-Step Accounting Eq  │
       │ Weighted Confidence   │   │ Outliers & 1000x Misreads │   │ Diagnostic Attribution│
       └───────────┬───────────┘   └─────────────┬─────────────┘   └───────────┬───────────┘
                   │                             │                             │
                   └─────────────────────────────┼─────────────────────────────┘
                                                 │
                                                 ▼
                                   ┌───────────────────────────┐
                                   │  app/ & data_verify/      │
                                   │  FastAPI REST API         │
                                   │  Source Text Grounding    │
                                   │  HITL Audit Trail         │
                                   └───────────────────────────┘
```

---

## 3. Subsystem Breakdown

### 3.1. ASC 280 Segment Validation & Confidence Scorer (`segmentation/`)
- **Location**: [segmentation/](file:///e:/VAULT/segmentation)
- **Primary Modules**:
  - [segmentation/validator.py](file:///e:/VAULT/segmentation/validator.py): Validation orchestrator for segment and balance sheet equations.
  - [segmentation/confidence.py](file:///e:/VAULT/segmentation/confidence.py): Weighted 4-factor confidence scoring engine.
  - [segmentation/extractor.py](file:///e:/VAULT/segmentation/extractor.py): XBRL and HTML table parser extracting operating segments, consolidated revenue, and balance sheet facts.
  - [segmentation/cli.py](file:///e:/VAULT/segmentation/cli.py): CLI interface supporting single CIK runs, offline XBRL directories, JSON output, and anomaly injections.
  - [segmentation/test_segmentation.py](file:///e:/VAULT/segmentation/test_segmentation.py): Unit test suite running against real 10-K fixtures (Apple, Microsoft, Nvidia, Netflix, Coca-Cola).

#### Mathematical Rules & Formulas
1. **Segment Reconciliation (ASC 280)**:
   $$\sum (\text{Segment Revenues}) + \text{Corporate/Other} - \text{Intersegment Eliminations} = \text{Consolidated Total Revenue}$$
   *(If a company reports as a single operating unit, marked as `N/A - Single Segment` and skipped cleanly).*

2. **Balance Sheet Equation**:
   $$\text{Total Assets} = \text{Total Liabilities} + \text{Shareholders' Equity}$$

3. **Weighted Confidence Formula (Score from 0.00 to 1.00)**:
   $$\text{Confidence Score} = (0.40 \times S_{\text{grounding}}) + (0.25 \times S_{\text{label}}) + (0.20 \times S_{\text{unit}}) + (0.15 \times S_{\text{yoy}})$$
   - $S_{\text{grounding}}$ ($40\%$ weight): $1.0$ if extracted digits appear verbatim in the source snippet; $0.0$ if missing (flags hallucination). Accounting zero-dash placeholders (`$—`) are grounded cleanly for non-segment corporate lines.
   - $S_{\text{label}}$ ($25\%$ weight): $1.0$ for exact standard row match; $0.6$ if derived or combined from subtotal rows.
   - $S_{\text{unit}}$ ($20\%$ weight): $1.0$ if fiscal period and unit scale (`USD millions`) are detected in table headers; $0.5$ if estimated.
   - $S_{\text{yoy}}$ ($15\%$ weight): $1.0$ if Year-over-Year change is within normal range ($\le 25\%$); $0.3$ if sudden jump ($> 25\%$).

4. **Escalation Triggers**:
   - **Trigger A (Low Confidence)**: $\text{Confidence Score} < 0.85 \implies \text{HUMAN\_REVIEW}$.
   - **Trigger B (Hard Accounting Failure)**: Segment reconciliation mismatch or $\text{Total Assets} \neq \text{Total Liabilities} + \text{Equity} \implies \text{HUMAN\_REVIEW}$ forced across all balance sheet metrics regardless of score.

---

### 3.2. Year-on-Year Validation Engine (`YOY_model/`)
- **Location**: [YOY_model/](file:///e:/VAULT/YOY_model)
- **Primary Modules**:
  - [YOY_model/yoy.py](file:///e:/VAULT/YOY_model/yoy.py): Core YoY computation, scale validation, and sign-reversal checks.
  - [YOY_model/multi_period.py](file:///e:/VAULT/YOY_model/multi_period.py): 3+ period historical outlier detection via Z-score and Median Absolute Deviation (MAD).
  - [YOY_model/integration.py](file:///e:/VAULT/YOY_model/integration.py): Idempotent LangGraph node (`yoy_validation_node`), confidence adjustments, and HITL review generation.
  - [YOY_model/explain.py](file:///e:/VAULT/YOY_model/explain.py): Natural-language reasoning (rule-based deterministic templates with optional Gemini/Groq LLM fallback).
  - [YOY_model/demo.py](file:///e:/VAULT/YOY_model/demo.py): Interactive CLI runner with sample companies (Apple, Microsoft, Tesla, Walmart, Coca-Cola).
  - [YOY_model/thresholds.yaml](file:///e:/VAULT/YOY_model/thresholds.yaml): Default configurable thresholds (`revenue: 50%`, `net_income: 150%`, `total_assets: 30%`, `total_liabilities: 40%`, etc.).
  - [YOY_model/test_yoy.py](file:///e:/VAULT/YOY_model/test_yoy.py): Co-located test suite.

#### Anomaly Detection Types
- **Likely Unit / Scale Error**: Detected when value ratio $\sim 1000\times$ (e.g. thousands vs. millions misread in table header).
- **Sign Flip**: Reversal between profit and loss (e.g. $+100\text{M} \rightarrow -20\text{M}$); always flagged for human audit.
- **Large Change**: Change exceeding metric threshold (e.g. M&A or restructuring).
- **Approaching Threshold**: Warning issued when change reaches $\ge 75\%$ of threshold.

---

### 3.3. 7-Step Accounting Equation Engine & AI Diagnostic Model (`validation/` & `models/`)
- **Location**: [validation/](file:///e:/VAULT/validation), [models/](file:///e:/VAULT/models)
- **Primary Modules**:
  - [validation/accounting_equation.py](file:///e:/VAULT/validation/accounting_equation.py): Pure 7-step deterministic engine testing identities:
    - $F_1: AC + ANC = A$ (Current Assets + Noncurrent Assets = Total Assets)
    - $F_2: LC + LNC = L$ (Current Liabilities + Noncurrent Liabilities = Total Liabilities)
    - $F_3: L + E = LE$ (Liabilities + Equity = Liabilities & Equity)
    - $F_4: A = L + E$ (Total Assets = Liabilities + Equity)
    - $F_5: LE = A$ (Liabilities & Equity = Total Assets)
  - [models/confidence_ai.py](file:///e:/VAULT/models/confidence_ai.py): Calibrated Random Forest Classifier predicting root-cause categories (`CLEAN`, `WRONG_COLUMN`, `WRONG_ROW`, `MISSING_NCI_MEZZANINE`, `SCALE_ERROR`, `SIGN_ERROR`, `DERIVED_OR_SUBTOTAL_WARN`, `AMBIGUOUS_OR_MULTI_ERROR`).
  - [models/cik_data_loader.py](file:///e:/VAULT/models/cik_data_loader.py): CIK-only data loader parsing local Parquet tickers, XBRL filings, or CompanyFacts JSON.
  - [run_validation.py](file:///e:/VAULT/run_validation.py): Standalone CLI runner with deliberate error injection flags (`--inject-apple-error`).
  - [interactive_validation.py](file:///e:/VAULT/interactive_validation.py): Interactive terminal validation harness.

---

### 3.4. Source Grounding Veracity Engine (`data_verify/`)
- **Location**: [data_verify/](file:///e:/VAULT/data_verify)
- **Primary Modules**:
  - [data_verify/source_grounding.py](file:///e:/VAULT/data_verify/source_grounding.py): Validates that extracted figures exist verbatim in SEC HTML tables/snippets with regex word-boundary precision before calculation.
  - [data_verify/test_source_grounding.py](file:///e:/VAULT/data_verify/test_source_grounding.py): 6 test cases verifying parenthesized negatives, partial digit guards, and missing digit detection.

---

### 3.5. REST API & Human-in-the-Loop Audit Service (`app/`)
- **Location**: [app/](file:///e:/VAULT/app)
- **Primary Modules**:
  - [app/main.py](file:///e:/VAULT/app/main.py): FastAPI service entry point with static UI at `/`.
  - [app/api/yoy_routes.py](file:///e:/VAULT/app/api/yoy_routes.py): Endpoints `POST /api/v1/yoy/validate` and `POST /api/v1/yoy/review`.
  - [app/services/audit_logger.py](file:///e:/VAULT/app/services/audit_logger.py): Append-only audit logger writing to `audit_logs/yoy_audit_trail.jsonl`.
  - [app/services/unit_normalizer.py](file:///e:/VAULT/app/services/unit_normalizer.py): Currency and scale unit normalizer.

---

### 3.6. SEC EDGAR Toolkit (`edgartools/`)
- **Location**: [edgartools/](file:///e:/VAULT/edgartools)
- **Description**: Integrated editable SEC filing toolkit (`edgartools 6.0.0`). Contains offline XBRL test fixtures for Apple, Microsoft, Nvidia, Netflix, and Coca-Cola under `edgartools/tests/fixtures/xbrl/`.

---

## 4. Current Repository & Git State

- **Active Branch**: `main` (synced with `origin/main`).
- **Recent Git History**:
  - `df2b132`: Merge branch 'main' of remote repository.
  - `f16c8a5`: Unified [requirements.txt](file:///e:/VAULT/requirements.txt) covering all external dependencies (`fastapi`, `uvicorn`, `pydantic`, `httpx`, `pyyaml`, `numpy`, `pandas`, `pyarrow`, `lxml`, `scikit-learn`, `joblib`, `pytest`, `-e ./edgartools`).
  - `3f75058`: Created [YOY_model/](file:///e:/VAULT/YOY_model) package with co-located tests and root backward-compatibility forwarders.
  - `5bc0815`: Clean merge of `segmentation` branch into `main`.
- **Working Tree**: Clean (all changes committed and synchronized).

---

## 5. Verification & Test Suite Status

All unit and integration test suites pass:

| Test Suite | File Location | Tests | Status |
| :--- | :--- | :--- | :--- |
| **Segmentation & ASC 280** | [segmentation/test_segmentation.py](file:///e:/VAULT/segmentation/test_segmentation.py) | 14 | **PASS** |
| **Source Text Grounding** | [data_verify/test_source_grounding.py](file:///e:/VAULT/data_verify/test_source_grounding.py) | 6 | **PASS** |
| **YoY Validation Model** | [YOY_model/test_yoy.py](file:///e:/VAULT/YOY_model/test_yoy.py) | 10 | **PASS** |
| **YoY Regression Suite** | [tests/test_yoy.py](file:///e:/VAULT/tests/test_yoy.py) | 10 | **PASS** |
| **Total Verified** | | **40** | **100% OK** |

---

## 6. How to Run Each Component

### 1. Segmentation & Confidence Validator (Single Command by CIK)
```powershell
# Online live fetch from SEC EDGAR
.\.venv\Scripts\python.exe -m segmentation 0000320193

# Offline validation with local XBRL fixture
.\.venv\Scripts\python.exe -m segmentation 0000320193 --xbrl-dir edgartools/tests/fixtures/xbrl/aapl/10k_2023

# Simulate accounting equation failure anomaly
.\.venv\Scripts\python.exe -m segmentation 0000320193 --xbrl-dir edgartools/tests/fixtures/xbrl/aapl/10k_2023 --simulate-anomaly accounting-equation
```

### 2. Year-on-Year Consistency Model
```powershell
# Run demo for all 5 sample companies
python -m YOY_model

# Run demo for a single company
python -m YOY_model Apple

# Run root launcher
python demo_yoy.py Apple
```

### 3. Balance Sheet AI & 7-Step Accounting Engine
```powershell
# Run validation by CIK
python run_validation.py --cik 0000320193

# Run interactive terminal tester
python interactive_validation.py --cik 0000320193
```

### 4. Running the Test Suites
```powershell
# Run segmentation and source grounding tests
.\.venv\Scripts\python.exe -m unittest segmentation/test_segmentation.py data_verify/test_source_grounding.py -v

# Run YoY model unit tests
python -m unittest YOY_model/test_yoy.py tests/test_yoy.py -v
```

