# V.A.U.L.T — 3-Member Prompt Kit to Finish PS-01

*For MineArena 1.0, PS-01: Human-in-the-Loop Financial Filing Auditor Agent. Written for a team that vibe-codes with an AI assistant and does not know the jargon.*

---

## 0. Read this first (2 minutes)

**The idea.** Your existing V.A.U.L.T work is strong at *checking* numbers. The problem statement wants a complete *agent*: it fetches filings, extracts numbers, pauses for a human when unsure, resumes without redoing work, compares with SEC's official data, and keeps an unalterable log. Most of what is missing is the glue around your engines, plus the reviewer screen.

**One honest flag.** Rule 8 of the problem statement says all code must be written during the 24-hour hackathon. If the V.A.U.L.T modules existed before 12 November, tell the organisers openly what was pre-existing and what you built during the event. Being upfront is much safer than being found out.

**Priority if time is short.** Build in this order and stop when time runs out: (1) end-to-end flow with pause/resume, (2) dashboard, (3) accuracy report, (4) audit log, (5) bonus items (adaptive thresholds, MCP). Never start a bonus item while a mandatory item is broken.

### Plain-English glossary

| Term | What it means for you |
| :-- | :-- |
| **10-K** | A company's yearly report filed with the SEC. |
| **CIK** | A company's ID number at the SEC (Apple = 0000320193). |
| **XBRL** | Machine-readable tags on the numbers in a filing (e.g. "Assets"). |
| **Company Facts API** | SEC's official database of all tagged numbers. This is your **ground truth** (the answer key). |
| **Ground truth** | The trusted correct answer you grade your extraction against. It must NOT be used to decide approvals. |
| **Confidence score** | 0 to 1: how sure the system is that an extracted number is right. |
| **Threshold** | The cut-off. Below it, a human must review (your current rule: under 0.85). |
| **HITL** | Human-in-the-loop: a person approves, corrects or rejects flagged values. |
| **LangGraph** | A library that runs the agent as numbered steps (a graph) and can pause and resume it. |
| **Checkpoint** | A saved snapshot of the workflow so it can continue after a pause or crash. |
| **Interrupt** | The LangGraph command that pauses the workflow and waits for a human. |
| **Idempotent** | Safe to run twice: the second run changes nothing. Resume must not fetch, log or approve twice. |
| **Append-only log** | A file where you can only add lines, never edit or delete. |
| **Hash chain** | Each log line stores a fingerprint of the previous line, so tampering is detectable. |
| **Endpoint** | A URL on your backend that does one job (e.g. "submit a review"). |
| **FastAPI / Streamlit** | Backend server / the simple Python tool for the reviewer screen. |
| **MCP** | A standard way to expose functions as tools an AI agent can call (bonus). |
| **Branch / PR** | Your own copy of the code to work on, then merged into the shared one. |

---

## 1. Gap analysis: what you have vs what judges want

| Requirement (PS-01) | Status | Owner |
| :-- | :-- | :-- |
| 3.1 Fetch 10-Ks for 3-5 companies via EDGAR API | Partial (works on 5 offline fixtures; live fetch and caching need hardening) | **A** |
| 3.2 At least 8 metrics with period, unit, source evidence | Partial (revenue and balance sheet exist; operating cash flow, net income and others need one unified record) | **A** |
| 3.3 Confidence per value, configurable threshold | Mostly done (formula exists; needs one config file and per-metric output) | **A** and **B** |
| 3.4 Validation: equation, segments, YoY, pass/fail list | Done as separate engines; needs one combined result list | **A** |
| 3.5 Pause on low confidence or failed check | **Missing** | **B** |
| 3.6 Checkpoint, resume, no duplicate actions | **Missing** (biggest gap) | **B** |
| 3.7 Reviewer dashboard (all 6 abilities) | Only a static YoY page | **C** |
| 3.8 Ground-truth comparison and accuracy report | **Missing** | **A** (logic) and **C** (page) |
| 3.8 Append-only audit log with original, corrected, decision, timestamp | Partial (YoY logger only) | **B** |
| Bonus: MCP, variance explanations, adaptive thresholds | Variance and explanations exist; MCP and adaptive are missing | **B** and **C** |
| Demo, README, architecture explanation | **Missing** | **C** |

---

## 2. Team roles

| Member | Role | One-line mission | Folders they own (touch nothing else) |
| :-- | :-- | :-- | :-- |
| **A** | Data Engine | Turn a CIK into trustworthy scored metrics, checks and an accuracy report | `pipeline/` |
| **B** | Agent Brain | The pause/resume workflow, audit log and API | `agent/`, `app/api/runs_routes.py`, `config/` |
| **C** | Face and Proof | Reviewer dashboard, demo, README, end-to-end tests | `dashboard/`, `scripts/`, `README.md`, `docs/` (except CONTRACT.md) |

Existing folders (`segmentation/`, `YOY_model/`, `validation/`, `data_verify/`, `app/`) are **wrapped, not rewritten**. Only Member A may add small helper functions to them, and must say so in the group chat.

---

## 3. Collaboration rules (this is what prevents chaos)

1. **The contract comes first.** Before anyone codes, Member A runs Prompt A0 below to create `docs/CONTRACT.md`. It defines the shape of the data and the API URLs. All three read it and say "agreed" in the group chat. After that, **nobody changes it alone**: raise it in the chat, all three agree, then update.
2. **Everyone works in parallel using fakes.** B builds the workflow with fake data (`pipeline/stubs.py`). C builds the dashboard with fake JSON (`dashboard/mock_data.json`). When A's real code is ready, a one-line switch replaces the fakes.
3. **One branch per person**: `member-a`, `member-b`, `member-c`. Merge into `main` only at the sync points below.
4. **Git rhythm**: pull before you start, commit every 30 to 45 minutes, push every hour. Never leave work only on your laptop.
5. **Every new AI chat starts with the Master Context Prompt (section 4).** The AI forgets everything between chats.
6. **Tell the AI to test.** Every prompt ends with "write tests, run them, show me the output". If a test fails, paste the full error back; never say only "it doesn't work".
7. **Stop the AI from wandering.** If it wants to edit a file outside your folders, say no and tell the group chat.
8. **Group chat message template** (post at every push): `[A] pushed: <what works>. Needs from B/C: <anything>. Blocked: <yes/no>.`

### Sync points (scale the times to the hours you have left)

| Sync | When | Goal |
| :-- | :-- | :-- |
| **S0** | First 30 min | Contract agreed, branches created |
| **S1** | About 25% of remaining time | A: real extraction for 5 companies. B: graph runs on stub data. C: dashboard shows mock data |
| **S2** | About 55% | Merge all. Switch fakes to real. First full end-to-end run |
| **S3** | About 80% | Feature freeze. Only bug fixes. Record a backup demo video |
| **S4** | Last 2 hours | Demo rehearsal twice, README done, nothing new merged |

---

## 4. Master Context Prompt (paste at the start of EVERY AI chat)

```
You are my senior engineer pair-programmer. I am NOT technical: explain in plain English, keep answers short, and give exact commands for Windows PowerShell.

PROJECT: V.A.U.L.T, our entry for the hackathon problem "PS-01 Human-in-the-Loop Financial Filing Auditor Agent". Repo: E:\VAULT (Python, FastAPI, virtual env at .venv, run Python as .\.venv\Scripts\python.exe).

ALREADY BUILT AND PASSING (40 tests): segmentation/ (ASC 280 segment check and 4-factor confidence scorer), YOY_model/ (year-on-year, 1000x scale error, sign flip, multi-period outliers, explain.py), validation/accounting_equation.py (7-step accounting identity engine), data_verify/source_grounding.py (checks a number appears in the source snippet), app/ (FastAPI, yoy routes, append-only audit_logger.py), edgartools/ (SEC toolkit with offline fixtures for Apple, Microsoft, Nvidia, Netflix, Coca-Cola). Confidence rule: score under 0.85 means HUMAN_REVIEW.

GOAL: complete the full agent: fetch 10-K from SEC EDGAR, extract 8+ metrics (revenue, net income, total assets, total liabilities, shareholders' equity, operating cash flow + more) each with period, unit and source snippet; score confidence; run validations; auto-approve or pause for a human (LangGraph interrupt + SQLite checkpointer); human approves/edits/rejects in a dashboard; workflow resumes without repeating work; compare to SEC XBRL Company Facts API (ground truth) and produce an accuracy report; keep an append-only audit log.

RULES:
1. First read docs/CONTRACT.md and obey it exactly. If you think it must change, STOP and tell me instead of changing it.
2. Only create or edit files inside MY folders (listed in my task). Wrap existing modules; never rewrite them.
3. Never use the ground-truth data to decide approvals. Ground truth is only for grading afterwards.
4. Never invent financial numbers. If data is missing, return an explicit error or null.
5. Write small steps, add tests, run them, and show me the real output. Do not claim something works unless you ran it.
6. Ask before installing any new library, and add it to requirements.txt.
7. After each task, give me: (a) what you did in 3 lines, (b) how to run it, (c) the git commit message to use.

MY ROLE: <Member A: Data Engine / Member B: Agent Brain / Member C: Face and Proof>
MY FOLDERS: <paste from the role table>
```

---

## 5. Shared contract: Member A runs this first

### Prompt A0 (creates the contract for all three)

```
[Paste Master Context first.]
Create docs/CONTRACT.md. It is the single source of truth for all three teammates. Include exactly:

1. METRIC RECORD (JSON): metric_id (run_id:metric_name), run_id, cik, company, fiscal_year, metric_name (one of: revenue, net_income, total_assets, total_liabilities, shareholders_equity, operating_cash_flow, current_assets, current_liabilities, cash_and_equivalents, diluted_eps), value (number, full USD, not thousands), unit, period_start, period_end, xbrl_tag, source_snippet, source_url, confidence (0-1), confidence_breakdown (grounding, label, unit, yoy), is_derived (bool), flags (list of check results), status (AUTO_APPROVED | NEEDS_REVIEW | APPROVED | CORRECTED | REJECTED), escalation_reason (text or null), final_value (null until decided).
2. CHECK RESULT (JSON): check_name (accounting_equation | segment_reconciliation | yoy_change | scale_error | sign_flip | source_grounding), passed (bool), severity (hard | soft), affected_metrics, message.
3. AUDIT LOG LINE (JSON): audit_id, run_id, metric_id, original_value, corrected_value (or null), decision (approve | correct | reject), reviewer, comment, timestamp_utc, prev_hash, hash.
4. API ENDPOINTS: POST /api/v1/runs {cik, inject_error?} returns run_id; GET /api/v1/runs; GET /api/v1/runs/{run_id} (status, metrics, checks, counts); GET /api/v1/reviews?status=pending|completed; POST /api/v1/runs/{run_id}/review {metric_id, decision, corrected_value?, reviewer, comment}; GET /api/v1/runs/{run_id}/accuracy-report; GET /api/v1/audit-log; GET /api/v1/audit-log/verify; GET and PUT /api/v1/config/thresholds.
5. PYTHON FUNCTIONS Member A must provide in pipeline/: fetch_filing(cik, fiscal_year), extract_metrics(filing), score_metrics(records), run_validations(records, history), fetch_ground_truth(cik, fiscal_year), build_accuracy_report(records, ground_truth). State input and output types of each.
6. FOLDER OWNERSHIP table (A: pipeline/, B: agent/, app/api/runs_routes.py, config/, C: dashboard/, scripts/, README.md, docs/).
7. CONFIG: config/thresholds.yaml with confidence_threshold: 0.85, yoy thresholds per metric, auto_approve_enabled: true.

Also create pipeline/stubs.py with fake versions of the 6 functions returning realistic hard-coded data for Apple (include one metric below 0.85 and one failed check so others can test escalation), and dashboard/mock_data.json with 2 sample runs in the contract's shape.
Run nothing risky; just show me the files and a summary table.
```

---

## 6. MEMBER A: Data Engine (`pipeline/`)

**Goal:** a CIK goes in; scored metrics, a combined list of checks and an accuracy report come out.

### A1: SEC client with caching

```
[Master Context, role A.] Create pipeline/edgar_client.py. Requirements: (1) fetch the list of 10-K filings for a CIK from SEC EDGAR (data.sec.gov/submissions) and download the filing's main document and XBRL; (2) SEC requires a descriptive User-Agent header like "TeamName your@email.com" read from a .env variable SEC_USER_AGENT, and a limit of 10 requests per second, so add a polite delay and retry with backoff; (3) cache every response on disk in data/cache/ so repeat runs never hit the network; (4) if offline or the live call fails, fall back to the local fixtures in edgartools/tests/fixtures/xbrl/ and clearly mark the result source as "offline_fixture"; (5) support these 5 companies by CIK: Apple 0000320193, Microsoft 0000789019, Nvidia 0001045810, Netflix 0001065280, Coca-Cola 0000021344. Implement fetch_filing(cik, fiscal_year) from the contract. Write tests with mocked network and one real live smoke test that I can run manually. Show test output.
```

### A2: Extract 10 metrics with evidence

```
[Master Context, role A.] Create pipeline/extractor.py implementing extract_metrics(filing) per docs/CONTRACT.md. Reuse segmentation/extractor.py where it already works; do not duplicate it. Extract these 10 metrics: revenue, net_income, total_assets, total_liabilities, shareholders_equity, operating_cash_flow, current_assets, current_liabilities, cash_and_equivalents, diluted_eps. Rules: (1) revenue is tagged differently across companies (Revenues, RevenueFromContractWithCustomerExcludingAssessedTax, SalesRevenueNet), so try a priority list of tags and record which tag was used in xbrl_tag; (2) if total_liabilities is not tagged, derive it as (Liabilities and Equity minus Equity) and set is_derived=true; (3) every record must include period_start, period_end, unit and a source_snippet, which is the real table row text from the filing HTML containing the number; (4) values in full USD (apply the scale from the table header); (5) never guess: if a metric is missing, return the record with value null and an escalation_reason. Test on all 5 offline fixtures and print a table of company by metric showing the values. Show it to me.
```

### A3: One scoring and validation pipeline

```
[Master Context, role A.] Create pipeline/scoring.py (score_metrics) and pipeline/validators.py (run_validations). Scoring: call the existing segmentation/confidence.py 4-factor formula (0.40 grounding, 0.25 label, 0.20 unit, 0.15 yoy) for EVERY metric and fill confidence and confidence_breakdown; read the threshold from config/thresholds.yaml (default 0.85), do not hard-code it. Validators: wrap the existing engines and return a single list of CheckResult objects per the contract: accounting equation (validation/accounting_equation.py), segment reconciliation (segmentation/validator.py; mark as passed with message "N/A single segment" when not applicable), year-on-year and scale and sign checks (YOY_model/yoy.py using prior-year values fetched from earlier 10-Ks, and multi_period.py when 3+ years exist), source grounding (data_verify/source_grounding.py). Hard failures (equation or segment mismatch) must attach to all affected metrics so they escalate regardless of score, exactly like Trigger B in the existing design. Write tests proving a clean Apple run passes and an injected error fails the right check. Show output.
```

### A4: Ground truth and accuracy report

```
[Master Context, role A.] Create pipeline/ground_truth.py and pipeline/accuracy.py. fetch_ground_truth(cik, fiscal_year): call the SEC Company Facts API (https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json, CIK padded to 10 digits, same User-Agent and cache rules as edgar_client). For each of our 10 metrics pick the matching us-gaap tag and the value where form is 10-K, fiscal year matches and the period end date matches. build_accuracy_report(records, ground_truth) must return: per metric match or mismatch (tolerance 0.5% for rounding), overall extraction accuracy percent, count AUTO_APPROVED, count escalated, count human APPROVED/CORRECTED/REJECTED, accuracy of auto-approved values only, ESCALATION PRECISION (escalated values that were truly wrong or truly anomalous divided by all escalated), and MISSED ERRORS (auto-approved but wrong, listed explicitly). Ground truth must only be used here, never earlier in the pipeline. Add a CLI: python -m pipeline.accuracy --cik 0000320193. Show the report for all 5 companies.
```

### A5: Error injection for the demo

```
[Master Context, role A.] Create pipeline/error_injection.py with deliberate, labeled corruptions applied to extracted records AFTER extraction: scale_1000x (value multiplied by 1000), sign_flip, wrong_column (use prior-year value), missing_digit (drop a digit but keep the snippet), swapped_assets_liabilities. Each injection must tag the record internally with injected_error=true so the accuracy report can compute escalation precision. Expose inject_error=<name> on fetch to extract flow. Write a test showing each injection gets escalated and a clean run gets mostly auto-approved. Create a table of: injection type, which check caught it, final confidence.
```

**Member A Definition of Done:** 5 companies by 10 metrics extracted with snippets; combined check list; accuracy report prints; every injection is caught.

---

## 7. MEMBER B: Agent Brain (`agent/`, `app/api/runs_routes.py`, `config/`)

**Goal:** the workflow that pauses, waits for a human, and resumes safely. **Start with the fakes in `pipeline/stubs.py`; do not wait for Member A.**

### B1: Config and tamper-evident audit log

```
[Master Context, role B.] (1) Create config/thresholds.yaml per the contract and a loader agent/config.py that can reload it at runtime. (2) Extend app/services/audit_logger.py (do not break its existing YoY tests) or create agent/audit.py implementing the AUDIT LOG LINE from docs/CONTRACT.md. It must be append-only: open the file only in append mode, never rewrite or delete, write to audit_logs/decisions.jsonl. Each line stores the hash of the previous line plus its own hash (SHA-256), making a hash chain. Add verify_chain() that returns true or the first broken line number. Add a unit test that edits one old line by hand and proves verify_chain() detects it. Show test output.
```

### B2: The LangGraph workflow with pause and resume

```
[Master Context, role B.] Create agent/graph.py, agent/state.py and agent/nodes.py using LangGraph. Steps, in order: fetch_filing, extract, score, validate, decide, human_review, apply_decisions, ground_truth_verify, final_report. Use pipeline/stubs.py for now; add an environment switch USE_STUBS=true/false so we can flip to real code with no edits. Requirements: (1) decide node: AUTO_APPROVED if confidence >= threshold AND no failed check touches the metric; otherwise NEEDS_REVIEW with a human-readable escalation_reason. (2) human_review node: if any NEEDS_REVIEW metrics exist, call langgraph interrupt() with a payload of all flagged metrics (value, confidence, snippet, reason, failed checks); if none, skip. (3) Persistence: use SqliteSaver checkpointer stored in data/checkpoints.sqlite, and thread_id = run_id. (4) Resume with Command(resume={decisions}). (5) apply_decisions: approve keeps the value, correct replaces it with corrected_value, reject sets status REJECTED and excludes it from the final report; each decision is written to the audit log. (6) IMPORTANT IDEMPOTENCY: LangGraph re-runs a paused node from its start on resume, so any code placed before interrupt() runs twice. Keep nodes before the interrupt free of side effects, and make every side effect (audit write, report write) keyed by an idempotency key (run_id plus metric_id plus step) so repeating it does nothing. Write tests: run to the pause, check state is saved, resume, verify each step ran exactly once. Show output.
```

### B3: API endpoints

```
[Master Context, role B.] Create app/api/runs_routes.py implementing every endpoint listed in docs/CONTRACT.md section 4 and register it in app/main.py (one-line change only). Details: POST /runs starts the graph in a background task and returns run_id immediately; GET /runs/{id} returns status (RUNNING | WAITING_FOR_REVIEW | COMPLETED | FAILED), all metrics, checks, and counts (auto_approved, escalated, pending, completed); POST review validates that the metric is actually pending and rejects duplicates with HTTP 409; when the last pending metric is decided, resume the graph. Add CORS so the Streamlit app can call it. Add FastAPI automatic docs check: open /docs. Write API tests with FastAPI TestClient. Show how to start the server and a curl-style example for each endpoint.
```

### B4: Prove reliability (judges test this)

```
[Master Context, role B.] Write tests/test_workflow_reliability.py proving: (1) pause happens when a low-confidence metric exists; (2) restarting the whole server while paused, then submitting a review, still resumes correctly from SQLite; (3) submitting the same review twice creates exactly ONE audit line; (4) fetch_filing is called exactly once per run even after resume (use a call counter); (5) a run with all high-confidence metrics never pauses; (6) reject and correct both flow into the final report correctly. Make a plain-English summary I can read out in the demo.
```

### B5 (bonus, only after everything mandatory works): Adaptive thresholds

```
[Master Context, role B.] Create agent/adaptive.py. Read past human decisions from the audit log. For each metric type, if humans approved at least 20 escalated values of that type with confidence between 0.75 and 0.85 and zero corrections, suggest lowering that metric's threshold by 0.02 (never below 0.75). Never adapt for hard accounting failures, never auto-apply: expose GET /api/v1/config/thresholds/suggestions and let the reviewer accept via PUT. Keep a test showing a real injected error is still escalated after adaptation. Explain it in 5 plain sentences.
```

### B6 (bonus): MCP tools

```
[Master Context, role B.] Create agent/mcp_server.py using the official Python MCP SDK (FastMCP) exposing two tools: fetch_10k_filing(cik, fiscal_year) and lookup_xbrl_fact(cik, tag, fiscal_year), implemented by calling pipeline/ functions. Show how to start it and how to list the tools. Keep it under 80 lines.
```

**Member B Definition of Done:** pause, restart, resume works; duplicate review gives 409 and one audit line; hash chain verifies; all endpoints respond.

---

## 8. MEMBER C: Face and Proof (`dashboard/`, `scripts/`, README, docs)

**Goal:** a reviewer screen judges can understand in 30 seconds, plus the demo story. **Start with `dashboard/mock_data.json`; do not wait for B.**

### C1: Dashboard skeleton on fake data

```
[Master Context, role C.] Create a Streamlit app in dashboard/app.py with a sidebar navigation of 5 pages: Overview, Review Queue, Accuracy Report, Audit Log, Settings. Create dashboard/api_client.py with one function per endpoint in docs/CONTRACT.md. Add a switch USE_MOCK=true/false: when true read dashboard/mock_data.json, when false call the FastAPI backend at http://localhost:8000. Build ONLY the skeleton and navigation now, with a clean consistent look (cards, color-coded status badges: green auto-approved, amber needs review, blue approved, purple corrected, red rejected). Show how to run it (streamlit run dashboard/app.py).
```

### C2: Review Queue (the page judges care about most)

```
[Master Context, role C.] Build the Review Queue page. Left: list of pending flagged metrics (company, metric, value, confidence). Right: detail panel showing (1) extracted value with unit and period, (2) confidence as a progress bar plus its 4-factor breakdown, (3) the ORIGINAL SOURCE SNIPPET with the extracted number highlighted in yellow, (4) the reason for escalation in plain English, (5) every validation check for that metric with green tick or red cross, (6) three buttons: Approve, Correct (opens a number box for the corrected value), Reject, plus required Reviewer Name and optional comment. Confirm before submitting; show success message; the item then moves to a Completed tab. Handle the case of a 409 duplicate gracefully. Also show a banner: "Workflow paused: N values waiting for review" or "Workflow resumed and completed".
```

### C3: Overview and run launcher

```
[Master Context, role C.] Build the Overview page: dropdown of the 5 companies, a checkbox "Inject a deliberate error" with a dropdown of injection types, and a Start Run button. Below: a metrics table (10 metrics, value, confidence, status badge), counters (auto-approved, escalated, pending reviews, completed reviews), and a validation panel listing every check as passed or failed with its message. Auto-refresh every 3 seconds while status is RUNNING or WAITING_FOR_REVIEW.
```

### C4: Accuracy, Audit and Settings pages

```
[Master Context, role C.] Build: (1) Accuracy Report page: overall accuracy percent, table per metric (extracted vs SEC ground truth vs match), counts of auto-approved vs escalated vs human-decided, ESCALATION PRECISION and MISSED ERRORS highlighted, a bar chart, and a Download CSV button. (2) Audit Log page: table of all decisions (original value, corrected value, decision, reviewer, timestamp), a filter by run, and a badge "Chain verified" or "TAMPERING DETECTED" from GET /api/v1/audit-log/verify. (3) Settings page: slider for the confidence threshold (0.50 to 0.99) that saves via PUT, with a note explaining what happens at lower and higher values.
```

### C5: End-to-end smoke test and demo script

```
[Master Context, role C.] Create scripts/smoke_test.py that starts a run for Apple with an injected scale_1000x error, waits for WAITING_FOR_REVIEW, submits a Correct decision via the API, waits for COMPLETED, then asserts: the audit log has exactly one new line, the accuracy report exists, and re-submitting the same decision is refused. Print PASS or FAIL clearly. Then create docs/DEMO_SCRIPT.md: a 6-minute demo in this order: (1) one-slide problem, (2) clean run on Microsoft: mostly auto-approved, (3) injected error run: workflow pauses, (4) reviewer inspects snippet and corrects, (5) KILL and restart the server, resume still works, (6) accuracy report and escalation precision, (7) audit log with chain verification. Include what to SAY at each step in simple language.
```

### C6: README, architecture, judge Q and A

```
[Master Context, role C.] Create README.md with: problem, architecture diagram as a Mermaid flowchart (EDGAR to extract to score to validate to decide to pause to human to resume to ground truth to report), how to install and run everything on Windows in under 10 steps, folder map, and a table mapping each of the 8 judging criteria (extraction accuracy, confidence scoring, auto-approval vs escalation, escalation precision, workflow reliability, human review experience, auditability, technical implementation) to the exact feature and file that satisfies it. Then create docs/JUDGE_QA.md with 15 likely judge questions and short honest answers (for example: how do you avoid duplicate actions on resume, why not trust the LLM, what is ground truth used for, what are your limitations).
```

**Member C Definition of Done:** a stranger can run a complete review in the dashboard with no help; smoke test prints PASS; README works from scratch.

---

## 9. Integration day prompts (all three, at S2)

### Prompt I1: Switch fakes to real (Member B, with A present)

```
[Master Context, role B.] Set USE_STUBS=false and run the full graph for all 5 companies. List every failure as a table: company, step, error, suspected owner (A, B or C). Fix only issues in my folders; for the rest, write a short bug report for the owner in the format: what I called, what I expected per docs/CONTRACT.md, what I got.
```

### Prompt I2: Bug-fixing template (use any time)

```
[Master Context.] Something is broken. What I did: <steps>. What I expected: <...>. What happened: <paste the FULL error text>. First explain in 3 plain-English lines what the error means, then propose the smallest fix inside my folders only, apply it, run the relevant tests and show me the output.
```

### Prompt I3: Pre-merge safety check (everyone, before merging into main)

```
[Master Context.] Before I merge: run the full test suite (all existing 40 tests plus new ones) and report pass or fail counts. List every file I changed outside my folders. Check that no API keys or .env files are staged for commit and that requirements.txt includes every new library. Show me a plain-English summary and the exact git commands to merge safely.
```

---

## 10. Final checklist before you present

| Judging criterion | Proof to show | Done |
| :-- | :-- | :-- |
| Extraction accuracy | Accuracy report for 5 companies by 10 metrics vs SEC Company Facts | ☐ |
| Confidence scoring | Per-metric score with 4-factor breakdown; configurable threshold | ☐ |
| Auto-approval vs escalation | Counters on dashboard; clean run mostly auto-approved | ☐ |
| Escalation precision | Number in accuracy report; injected errors all caught | ☐ |
| Workflow reliability | Kill-and-restart demo; reliability test passes | ☐ |
| Human review experience | Highlighted snippet, reason, checks, three buttons | ☐ |
| Auditability | Hash-chained log, verify badge, original and corrected values | ☐ |
| Technical implementation | README, architecture diagram, smoke test PASS | ☐ |

**Last advice.** Rehearse the demo twice on the exact laptop you will present from, with Wi-Fi off (so the offline fixture fallback is tested), and keep a screen recording as a backup. When judges ask something you do not know, the safe answer is the true one: say what the system does, what it does not do yet, and where in the log or report they can verify it.
