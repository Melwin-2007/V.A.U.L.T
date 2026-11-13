# V.A.U.L.T — 6-Minute Live Demo Script
**For MineArena 1.0 (PS-01: Human-in-the-Loop Financial Filing Auditor Agent)**

---

## Demo Timeline (6 Minutes Total)

```
00:00 - 01:00  Problem & The V.A.U.L.T Vision (Why AI Needs an Auditor)
01:00 - 02:15  Clean Run on Microsoft (Zero-Touch Auto-Approval)
02:15 - 03:30  Injected Error Run on Apple (Pause & Highlighted Evidence)
03:30 - 04:30  Server Kill & Resume Proof (SQLite Idempotency)
04:30 - 05:15  Ground Truth Accuracy & Escalation Precision
05:15 - 06:00  Cryptographic Audit Log & Q&A
```

---

### Step 1: The Problem (Minute 00:00 – 01:00)
- **What to show**: Slide or open [docs/CONTRACT.md](file:///e:/VAULT/docs/CONTRACT.md).
- **What to say**:
  > *"Large Language Models are great at reading, but they hallucinate numbers, confuse thousands with millions, flip negative signs, and pick the wrong column. In high-stakes financial auditing, you cannot trust an LLM on faith.*
  >
  > *We built **V.A.U.L.T**: an audit-grade agent that extracts financial data from SEC Form 10-Ks, proves every number with source text grounding, validates mathematical and accounting identities, pauses for a human when confidence is low, and resumes without re-doing work."*

---

### Step 2: Clean Run (Minute 01:00 – 02:15)
- **What to show**: Browser dashboard at `http://localhost:8000/` $\rightarrow$ Overview tab $\rightarrow$ Select **Microsoft Corp. (0000789019)** $\rightarrow$ Click **Launch Audit Run**.
- **What to say**:
  > *"Here is a clean run for Microsoft. The agent extracts all 10 standard metrics. It checks ASC 280 segment reconciliation, the balance sheet equation ($A = L + E$), and Year-on-Year stability.*
  >
  > *Notice that all 10 metrics scored above our 0.85 threshold and all math checks passed. The run automatically completed with zero human intervention."*

---

### Step 3: Injected Error Run & Workflow Pause (Minute 02:15 – 03:30)
- **What to show**: Overview tab $\rightarrow$ Select **Apple Inc. (0000320193)** $\rightarrow$ Select Injection: **scale_1000x** $\rightarrow$ Click **Launch Audit Run**.
- **What to say**:
  > *"Now we simulate a real-world error: a table header misread where numbers are multiplied by 1000.*
  >
  > *Watch what happens: the workflow immediately pauses with status `WAITING_FOR_REVIEW`. It does not guess.*
  >
  > *Opening the Review Queue: the human auditor sees the exact metric, the 4-factor confidence breakdown, the mathematical reason for escalation, and the original SEC source snippet with the numbers highlighted."*

---

### Step 4: Server Kill & Resume Proof (Minute 03:30 – 04:30)
- **What to show**: Press `Ctrl+C` in the terminal to kill the server, restart it, refresh the dashboard, click **Correct**, input `62146000000`, and submit.
- **What to say**:
  > *"Judges test reliability: what happens if the power dies while a review is pending?*
  >
  > *I just killed the server. When I bring it back up, our SQLite checkpointer restores the exact paused state. The agent does not re-fetch the filing or duplicate actions.*
  >
  > *I submit the correction. The workflow resumes seamlessly, records the human decision, and completes the run."*

---

### Step 5: Accuracy Report & Escalation Precision (Minute 04:30 – 05:15)
- **What to show**: Accuracy Report tab.
- **What to say**:
  > *"We compare our extractions against the official SEC Company Facts API—our answer key.*
  >
  > *Notice our two key metrics: **Overall Extraction Accuracy** and **Escalation Precision**—the percentage of flagged items that were genuinely wrong. We caught 100% of injected errors with zero missed errors."*

---

### Step 6: Cryptographic Audit Log (Minute 05:15 – 06:00)
- **What to show**: Cryptographic Audit Log tab showing the green `Chain Verified` badge.
- **What to say**:
  > *"Finally, compliance requires an unalterable paper trail. Every human decision is appended to a cryptographic SHA-256 hash chain.*
  >
  > *If anyone alters an old log line in the file, our `verify_chain` endpoint immediately detects the exact tampered line number. The ledger is mathematically tamper-evident."*

