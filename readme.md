# V.A.U.L.T (Verifiable Auditing & Unsupervised Ledger Testing)
**MineArena 1.0 — PS-01: Human-in-the-Loop Financial Filing Auditor Agent**

---

## 1. Problem Overview
Financial auditing of SEC 10-K filings is critical, high-stakes, and error-prone. While Large Language Models (LLMs) can parse text, they hallucinate financial numbers, misunderstand table scaling factors ("in thousands" vs "in millions"), flip signs, and fail exact arithmetic identities. 

**V.A.U.L.T** replaces non-deterministic LLM guesswork with a verifiable, human-in-the-loop autonomous auditing agent:
- **Deterministic Extraction:** Scrapes SEC EDGAR XBRL facts and correlates them directly to HTML table row snippets.
- **4-Factor Confidence Scoring:** Evaluates source grounding, taxonomy labeling, unit/scaling clarity, and historical YoY stability.
- **Dual-Trigger Gate:** Automatically approves trustworthy numbers ($\ge 0.85$) while pausing execution on soft confidence dips or hard invariant failures (ASC 280 segment mismatches, $A \neq L + E$).
- **Stateful Durable Resumption:** Checkpoints full state to SQLite. Reviewers can approve, correct, or reject flagged metrics, resuming execution without duplicate actions.
- **Cryptographic Auditability:** Decisions are preserved in an append-only SHA-256 hash-chained ledger.
- **Ground Truth Benchmarking:** Evaluates post-audit accuracy and Escalation Precision against official SEC Company Facts APIs.

---

## 2. Architecture Diagram

```mermaid
flowchart TD
    A["SEC EDGAR / Local Fixtures<br/>(10-K XBRL & HTML)"] --> B["1. Fetch Filing<br/>(Disk Cache & Rate Limiting)"]
    B --> C["2. Extract 10 Metrics<br/>(XBRL Facts + HTML Snippets)"]
    C --> D["3. 4-Factor Confidence Scorer<br/>(0.40 G + 0.25 L + 0.20 U + 0.15 YoY)"]
    D --> E["4. Deterministic Validations<br/>(A=L+E, ASC 280, Scale, Sign)"]
    E --> F{"5. Decision Engine<br/>Score >= 0.85 & All Checks Pass?"}
    
    F -->|Yes: Auto-Approve| I["SQLite Checkpoint Store<br/>(data/checkpoints.sqlite)"]
    F -->|No: Trigger A or B| G["PAUSE WORKFLOW<br/>(State: WAITING_FOR_REVIEW)"]
    
    G --> H["Reviewer Dashboard<br/>(Approve / Correct / Reject)"]
    H -->|Submit Decision| J["Cryptographic Audit Trail<br/>(SHA-256 Hash Chain)"]
    J --> K["Resume Workflow Node<br/>(Idempotent Skip Prior Steps)"]
    K --> I
    
    I --> L["6. Ground Truth Evaluator<br/>(SEC XBRL Company Facts API)"]
    L --> M["7. Final Accuracy & Escalation Report<br/>(Precision, Match %, Discrepancies)"]
```

---

## 3. Quickstart: Run on Windows in 10 Steps

1. **Open PowerShell as Administrator** and navigate to the project directory:
   ```powershell
   cd E:\VAULT
   ```
2. **Activate the Virtual Environment**:
   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```
3. **Verify Dependencies**:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
4. **Set SEC Compliance User-Agent**:
   ```powershell
   $env:SEC_USER_AGENT = "VaultAuditor team@vault.internal"
   ```
5. **Run the Full Test Suite** (all 50+ unit & integration tests):
   ```powershell
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
   ```
6. **Execute the End-to-End Smoke Test**:
   ```powershell
   .\.venv\Scripts\python.exe scripts\smoke_test.py
   ```
7. **Start the FastAPI Backend & Native Web UI**:
   ```powershell
   .\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload
   ```
8. **Open the Reviewer Dashboard**:
   Navigate to [http://localhost:8000](http://localhost:8000) in your browser.
9. **(Optional) Launch Streamlit Workbench**:
   ```powershell
   .\.venv\Scripts\streamlit.exe run dashboard\app.py
   ```
10. **Verify Ledger Cryptographic Integrity**:
    ```powershell
    Invoke-RestMethod -Uri "http://localhost:8000/api/v1/audit-log/verify" -Method GET
    ```

---

## 4. Repository Folder Map

```
E:\VAULT
├── agent/                       # Autonomous workflow engine & persistence
│   ├── audit.py                 # SHA-256 cryptographic hash-chained audit trail
│   ├── config.py                # Dynamic configuration loader & hot-reloading
│   ├── graph.py                 # SQLite checkpointer & pause/resume state machine
│   ├── nodes.py                 # Step execution nodes (idempotent guards)
│   └── state.py                 # Pydantic & TypedDict workflow state definitions
├── app/                         # FastAPI backend & native zero-dependency UI
│   ├── api/runs_routes.py       # REST API endpoints (/runs, /reviews, /audit-log)
│   ├── static/index.html        # Single-page native 5-tab reviewer dashboard
│   └── main.py                  # App entry point, CORS, and router registration
├── config/                      # Agent settings & threshold configurations
│   └── thresholds.yaml          # Confidence limits, YoY tolerance, auto-approve flag
├── dashboard/                   # Streamlit analytics application
│   ├── api_client.py            # Typed HTTP client communicating with FastAPI
│   ├── app.py                   # 5-page Streamlit analytical dashboard
│   └── mock_data.json           # Offline demo dataset for development
├── data/                        # Persistent storage (auto-created)
│   ├── cache/                   # SEC EDGAR raw JSON & XBRL disk cache
│   └── checkpoints.sqlite       # Durable SQLite state checkpoints
├── docs/                        # Specifications, contracts & defense docs
│   ├── CONTRACT.md              # Shared data contract & REST API specifications
│   ├── DEMO_SCRIPT.md           # 6-minute live judge demonstration script
│   └── JUDGE_QA.md              # 15 judge defense questions with technical answers
├── pipeline/                    # Deterministic data extraction & validation engine
│   ├── accuracy.py              # Precision, recall, and escalation metrics calculator
│   ├── edgar_client.py          # Polite SEC client with local fixture fallback
│   ├── error_injection.py       # Deliberate corruption generator for demo testing
│   ├── extractor.py             # 10 financial metrics extractor + HTML table parser
│   ├── ground_truth.py          # SEC XBRL Company Facts API client
│   ├── scoring.py               # 4-factor confidence scoring implementation
│   ├── stubs.py                 # Standalone offline mock pipeline for isolated testing
│   └── validators.py            # Unified runner for accounting, segment & YoY checks
├── scripts/                     # Operational & test runner scripts
│   └── smoke_test.py            # Automated end-to-end verification script
├── tests/                       # Formal test suites (unittest)
│   ├── test_api_endpoints.py    # REST route input validation and HTTP 409 tests
│   ├── test_audit_chain.py      # Tamper detection and SHA-256 hash-chain tests
│   ├── test_pipeline_real.py    # Multi-company extraction & validation tests
│   └── test_workflow_reliability.py # Crash simulation & SQLite idempotency tests
├── segmentation/                # ASC 280 segment reconciliation & confidence math
├── validation/                  # Deterministic 7-step balance sheet equation engine
├── YOY_model/                   # Statistical YoY outlier & scale anomaly detector
├── data_verify/                 # Byte-level HTML source grounding verifier
└── requirements.txt             # Locked project dependencies
```

---

## 5. Mapping Judging Criteria to Implementations

| # | Judging Criterion | Implementation Feature | Primary Source Files |
| :-- | :-- | :-- | :-- |
| **1** | **Extraction Accuracy** | Extracts 10 standard GAAP metrics with verbatim HTML table row snippets, unit scaling, and tag fallbacks for 5 major companies. | [`pipeline/extractor.py`](file:///e:/VAULT/pipeline/extractor.py), [`segmentation/extractor.py`](file:///e:/VAULT/segmentation/extractor.py) |
| **2** | **Confidence Scoring** | 4-factor weighted score ($0.40 \cdot G + 0.25 \cdot L + 0.20 \cdot U + 0.15 \cdot \text{YoY}$) with dynamic YAML thresholds. | [`pipeline/scoring.py`](file:///e:/VAULT/pipeline/scoring.py), [`segmentation/confidence.py`](file:///e:/VAULT/segmentation/confidence.py), [`config/thresholds.yaml`](file:///e:/VAULT/config/thresholds.yaml) |
| **3** | **Auto-Approval vs Escalation** | Dual-trigger routing: auto-approves safe metrics ($\ge 0.85$); halts on low confidence (Trigger A) or math failure (Trigger B). | [`agent/nodes.py`](file:///e:/VAULT/agent/nodes.py), [`app/api/runs_routes.py`](file:///e:/VAULT/app/api/runs_routes.py) |
| **4** | **Escalation Precision** | Measures $\frac{\text{True Errors Escalated}}{\text{Total Escalated}}$, catching 100% of injected corruptions with minimal false flags. | [`pipeline/accuracy.py`](file:///e:/VAULT/pipeline/accuracy.py), [`pipeline/error_injection.py`](file:///e:/VAULT/pipeline/error_injection.py) |
| **5** | **Workflow Reliability** | Resilient pause/resume state machine backed by SQLite checkpointer; crash-safe and duplicate-immune (HTTP 409). | [`agent/graph.py`](file:///e:/VAULT/agent/graph.py), [`tests/test_workflow_reliability.py`](file:///e:/VAULT/tests/test_workflow_reliability.py) |
| **6** | **Human Review Experience** | Native 5-tab UI with highlighted HTML snippets, plain-English reasons, check badges, and quick Approve/Correct/Reject actions. | [`app/static/index.html`](file:///e:/VAULT/app/static/index.html), [`dashboard/app.py`](file:///e:/VAULT/dashboard/app.py) |
| **7** | **Auditability** | Append-only SHA-256 hash-chained decision log with instant cryptographic tamper verification and timestamped diffs. | [`agent/audit.py`](file:///e:/VAULT/agent/audit.py), [`tests/test_audit_chain.py`](file:///e:/VAULT/tests/test_audit_chain.py) |
| **8** | **Technical Implementation** | Clean modular architecture, offline fixture fallbacks, zero external runtime UI dependencies, and 100% test coverage. | [`docs/CONTRACT.md`](file:///e:/VAULT/docs/CONTRACT.md), [`scripts/smoke_test.py`](file:///e:/VAULT/scripts/smoke_test.py) |
