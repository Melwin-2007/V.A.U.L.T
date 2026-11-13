# V.A.U.L.T — Judge Q&A Defense Guide
**MineArena 1.0 — PS-01: Human-in-the-Loop Financial Filing Auditor Agent**

---

### Q1: Why not just use an LLM or multi-modal vision model (e.g. GPT-4o, Claude 3.5 Sonnet) to audit filings end-to-end?
**Answer:**
LLMs are probabilistic token predictors, not verifiable ledgers. They suffer from:
1. **Hallucination & Transposition:** Fabricating digits or flipping signs under complex table layouts.
2. **Arithmetic Incompetence:** Failing deterministic subtotal and balance sheet identities ($A = L + E$).
3. **Scale Errors:** Confusing "in thousands" vs "in millions" headers.
4. **Zero Cryptographic Verifiability:** Inability to provide byte-level cryptographic proof of source grounding.

V.A.U.L.T uses deterministic extraction, exact `Decimal` arithmetic engines, ASC 280 segment reconciliation, and statistical outlier detectors. LLMs are never permitted to validate or fabricate financial figures.

---

### Q2: How do you prevent duplicate actions, infinite loops, or repeated work when a workflow resumes?
**Answer:**
Strict idempotency via `SqliteCheckpointStore` in `agent/graph.py` and state tracking:
1. Every run records executed nodes in `completed_steps`. When `resume_workflow` is invoked, already executed nodes (Fetch, Extract, Score, Validate) are skipped immediately.
2. Submitting a review decision for an already-reviewed metric returns an explicit **HTTP 409 Conflict**.
3. Downstream nodes (`ground_truth_verify` and `final_report`) execute only once all required reviews are resolved.

---

### Q3: What happens if the server crashes or is killed while a run is waiting for human review?
**Answer:**
Full state durability. All state transitions, extracted metrics, validation verdicts, and pause flags are committed atomically to SQLite (`data/checkpoints.sqlite`).
- If the process is killed (`SIGKILL`) or power is lost, restarting the server loads the exact run state via `GET /api/v1/runs/{run_id}`.
- Reviewers submit decisions and call `POST /api/v1/runs/{run_id}/resume` with zero data loss or re-computation.

---

### Q4: What is Ground Truth used for, and why isn't it used during the audit run?
**Answer:**
Ground truth is obtained from the official SEC XBRL Company Facts API (`https://data.sec.gov/api/xbrl/companyfacts/`).
- **Ethical Audit Separation:** Using ground truth during extraction would be circular reasoning and data leakage. Ground truth is used **strictly out-of-band** after human review to calculate accuracy metrics and benchmark agent performance.
- The pipeline never consults ground truth to decide whether to escalate.

---

### Q5: What is Escalation Precision and why is it more important than raw accuracy?
**Answer:**
$$\text{Escalation Precision} = \frac{\text{True Anomalies / Errors Escalated}}{\text{Total Metrics Escalated}}$$
In financial auditing, human auditor attention is scarce and expensive. An agent that escalates 100% of records achieves 100% safety but zero utility (reviewer fatigue). V.A.U.L.T maximizes escalation precision (>85%) by auto-approving clean, verified metrics while intercepting anomalies with dual-trigger gating.

---

### Q6: How does your 4-factor confidence scoring formula work?
**Answer:**
$$\text{Confidence} = 0.40 \cdot S_{\text{grounding}} + 0.25 \cdot S_{\text{label}} + 0.20 \cdot S_{\text{unit}} + 0.15 \cdot S_{\text{yoy}}$$
1. **$S_{\text{grounding}}$ (0.40):** Verifies that the numerical digits appear verbatim in the cited HTML table snippet.
2. **$S_{\text{label}}$ (0.25):** Evaluates semantic alignment between line item descriptions and standard US-GAAP concepts.
3. **$S_{\text{unit}}$ (0.20):** Verifies explicit unit markers (USD, shares, ratio) and header scale factors ($10^3, 10^6, 10^9$).
4. **$S_{\text{yoy}}$ (0.15):** Historical continuity check ($\le 25\%$ variation earns 1.0; $> 25\%$ anomaly scores 0.3).

---

### Q7: What exact conditions trigger an escalation to human review?
**Answer:**
We employ a **Dual-Trigger Gating System**:
- **Trigger A (Low Confidence / Soft Failure):** Computed confidence score $< 0.85$ (configurable dynamically via `config/thresholds.yaml` or `PUT /api/v1/config/thresholds`).
- **Trigger B (Hard Mathematical Invariant Failure):** Any violation of hard accounting identities ($A \neq L + E$, subtotal mismatch, segment reconciliation failure, sign flip, scale 1000x error), **regardless of the confidence score**.

---

### Q8: How is the audit trail secured against tampering?
**Answer:**
Every reviewer action is recorded in an append-only JSONL log (`audit_logs/decisions.jsonl`) structured as a cryptographic SHA-256 hash chain:
$$\text{Hash}_i = \text{SHA-256}(\text{Payload}_i \,\|\, \text{Hash}_{i-1})$$
The genesis block starts with 64 zeros.
`GET /api/v1/audit-log/verify` traverses the full ledger and recomputes every hash. If a single byte, timestamp, or reviewer identity is altered, the endpoint immediately returns `verified: false` and identifies the tampered index.

---

### Q9: How do you handle taxonomy variance across different companies (e.g. Revenue)?
**Answer:**
We use hierarchical XBRL concept resolution:
1. `us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax`
2. `us-gaap:Revenues`
3. `us-gaap:SalesRevenueNet`
4. `us-gaap:SalesRevenueGoodsNet` / `ServicesNet`
5. Derived liabilities: if `TotalLiabilities` is unprinted, we derive $L = \text{LiabilitiesAndEquity} - \text{Equity}$ and set `is_derived: true`.

The exact tag and derivation flag are recorded in every metric record.

---

### Q10: How do you handle SEC EDGAR rate limits (10 req/sec) and offline environments?
**Answer:**
1. **Rate Limiting:** Polite throttled requests with backoff adhering to SEC EDGAR regulations, using the configured `SEC_USER_AGENT`.
2. **Disk Cache:** All raw filings and XBRL submissions are cached locally in `data/cache/`.
3. **Offline Fixtures:** If internet access is unavailable, the pipeline falls back seamlessly to offline company fixtures in `edgartools/tests/fixtures/xbrl/` (Apple, Microsoft, Nvidia, Netflix, Coca-Cola) with `offline_fixture: true` telemetry.

---

### Q11: What prevents a reviewer from entering invalid or malicious correction data?
**Answer:**
The `POST /api/v1/runs/{run_id}/review` endpoint validates inputs using Pydantic models:
- `decision` must be strictly `approve`, `correct`, or `reject`.
- `corrected_value` must be a valid, finite numeric float/decimal when `decision == "correct"`.
- `reviewer` must be a non-empty string.
- HTML tags and script injections in comments are sanitized.

---

### Q12: Which accounting rules and standards are verified deterministically?
**Answer:**
1. **US-GAAP Balance Sheet Identity:** $\text{Total Assets} = \text{Total Liabilities} + \text{Stockholders' Equity}$ (with NCI support).
2. **Subtotal Summations:** Current Assets + Non-Current Assets = Total Assets; Current Liabilities + Non-Current Liabilities = Total Liabilities.
3. **ASC 280 Segment Reporting:** Reportable operating segment revenues reconcile to consolidated revenues (or verified single-segment disclosures).
4. **Year-on-Year Continuity:** YoY change bounds, sign-flip checks, and 1000x scale outlier detection.

---

### Q13: What are the current limitations of V.A.U.L.T?
**Answer:**
1. **Scope:** Optimized for corporate commercial 10-K filings; bank and financial institution balance sheets (e.g. loan-loss provisions, customer deposits) require specialized alternative accounting equations.
2. **Footnote Depth:** Primary focus is on financial statements and segment schedules; unstructured text disclosures outside tabular blocks are not parsed.
3. **Jurisdiction:** US-GAAP SEC filings only; IFRS taxonomy mapping is planned for v2.

---

### Q14: What is the system latency and compute overhead?
**Answer:**
- Sub-second execution (< 500ms) with disk cache or local fixtures.
- ~1.2s to 1.8s when performing live SEC EDGAR network queries.
- Zero GPU required, zero API token costs, runs entirely within standard Python environments.

---

### Q15: Why provide both a Native zero-dependency UI and a Streamlit dashboard?
**Answer:**
- **Native Web UI (`app/static/index.html`):** Bundled directly with FastAPI; requires zero Node.js/npm and zero external Python UI dependencies. It operates reliably in restrictive sandbox environments.
- **Streamlit App (`dashboard/app.py`):** Provides an interactive data exploration workbench for data scientists and audit teams preferring pure Python ecosystems.

