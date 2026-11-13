# V.A.U.L.T — UI Redesign Spec (design.md)

**Product name:** V.A.U.L.T
**Expansion (shown under the logo):** Verifiable Auditing & Unsupervised Ledger Testing
**Page title / header line:** `V.A.U.L.T | Human-in-the-Loop SEC 10-K Financial Auditor`
**Audience:** professional financial auditors. They want evidence, exact numbers, and a trail they can defend. They do not want a toy or a flashy dashboard.
**Primary job of the UI:** let an auditor type a CIK, watch each audit agent work in a visible terminal, see exactly what it used and calculated, step in when a checkpoint fails, and walk away with an accuracy report and a tamper-evident audit log.

> **Instruction to the AI building this:** Modify the EXISTING UI (the one in the screenshot with the "Launchpad" card, the stepper, and the Live API / Pitch Demo toggle). Keep its teal-on-light theme, card layout and stepper. Do not switch frameworks. Replace every occurrence of "SATYA" and "Tri-Agent" with V.A.U.L.T branding as specified below. Do not change backend contracts in `docs/CONTRACT.md` without telling me.

---

## 1. Branding changes (find and replace everywhere)

| Old | New |
| :--- | :--- |
| SATYA | V.A.U.L.T |
| Verifiable Ledger Testing & Autonomous Tri-Agent Auditing | Verifiable Auditing & Unsupervised Ledger Testing |
| "Tri-Agent Invariants" and similar step names | Names defined in section 4 |
| Emoji on company presets | Two-letter monogram tiles (AA, MS, NV, KO, NF). Emoji look unprofessional to auditors |
| Browser tab title | `V.A.U.L.T — SEC 10-K Auditor` |

**Logo:** a simple wordmark "V.A.U.L.T" in semibold with letter-spacing, preceded by a small square mark (a rounded square in primary teal with a white keyhole or checkmark glyph drawn in SVG). No gradients.

---

## 2. Design tokens (keep the current theme, make it more rigorous)

### Colors
| Token | Hex | Use |
| :--- | :--- | :--- |
| `--bg` | `#F1F3F2` | Page background (cool light grey-green, as in the screenshot) |
| `--surface` | `#FFFFFF` | Cards, tables |
| `--border` | `#DCE1DF` | Card and table borders, 1px |
| `--ink` | `#1B2A2A` | Primary text |
| `--ink-muted` | `#5B6B6A` | Secondary text, helper text |
| `--primary` | `#1F6F66` | Buttons, active step, links (the teal from the screenshot) |
| `--primary-soft` | `#E1F1EE` | Active step background, success badge background |
| `--term-bg` | `#0E1A1B` | Terminal background |
| `--term-ink` | `#C9D6D4` | Terminal default text |
| `--pass` | `#2E9E6B` | Passed checks |
| `--warn` | `#C98A12` | Soft warnings (approaching threshold) |
| `--fail` | `#C23B3B` | Hard failures |
| `--human` | `#5B4FC4` | Human review required / human decision |
| `--info` | `#3B7FB8` | Neutral info lines |

Every status colour must be paired with an icon or text label. Never rely on colour alone.

### Typography
- **UI text:** a clean humanist sans (Inter, or the system stack `"Segoe UI", system-ui, sans-serif` as fallback).
- **Terminal, thread IDs, hashes, every number in a table:** JetBrains Mono (fallback `Consolas, monospace`) with `font-variant-numeric: tabular-nums`. Numbers must align by decimal place.
- **Scale:** 12 / 13 / 14 / 16 / 20 / 28 px. Body 14px. Line length under 80 characters.
- **Labels:** sentence case ("Central Index Key"), not tracked-out ALL CAPS.

### Shape and spacing
- Radius: 10px cards, 6px inputs and buttons, 4px badges. Shadows: none on cards (border only); a single soft shadow only on the human-review drawer so it reads as "on top".
- Spacing grid: 4px base. Card padding 24px. Gap between cards 16px.
- Page max width 1280px, centred. The agent screens use the full width because the terminal needs room.

---

## 3. Global layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [■] V.A.U.L.T | Human-in-the-Loop SEC 10-K Financial Auditor              │
│     Verifiable Auditing & Unsupervised Ledger Testing                     │
│                         MODE (•)Live API ( )Pitch Demo   THREAD badge  ⚙ │
├──────────────────────────────────────────────────────────────────────────┤
│ STEPPER (7 steps, sticky, horizontally scrollable on mobile)              │
├──────────────────────────────────────────────────────────────────────────┤
│ CHECKPOINT BAR  CP-02 saved · a91f…c3 · 14:02:11 UTC · run_2023_320193…   │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│                     ACTIVE STEP CONTENT                                   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
        (Human Review drawer slides in from the right over any step)
```

- Keep the **Mode toggle** (Live API / Pitch Demo) and the **Settings** button as they are.
- Keep the **thread badge**, but make it live: it shows `THREAD: run_<year>_<cik>_<hash>` and a status chip: `Running`, `Waiting for review`, `Resumed`, `Verified & completed`, `Failed`.
- The **checkpoint bar** is new. It always shows the last saved checkpoint (id, short hash, timestamp). It is the visual proof of persistence.

---

## 4. The stepper (7 steps, replaces the current 8)

| # | Step | Sub-label | Runs |
| :--- | :--- | :--- | :--- |
| 1 | Filing retrieval | SEC EDGAR | Terminal |
| 2 | Extraction output | 10 metrics | Result table |
| 3 | Asset Auditing Agent | Accounting equation | Terminal + result |
| 4 | Segment Validation Agent | ASC 280 | Terminal + result |
| 5 | YoY Comparing Agent | Multi-period | Terminal + result |
| 6 | Final verification | XBRL ground truth | Report |
| 7 | Audit history | Append-only log | Table |

Step states (each has a distinct icon and colour):
- **Pending:** grey outline circle, muted text.
- **Running:** teal ring with a subtle spinner (only this step animates).
- **Passed:** solid teal circle with a check.
- **Human review:** purple circle with a person glyph, label "Awaiting reviewer".
- **Failed / rejected:** red circle with an x.
- **Resumed:** teal check with a small circular-arrow badge.

Steps already completed are clickable (read-only review of their terminal log and output). The running step is auto-selected. Future steps are not clickable.

---

## 5. Step 1: Filing retrieval (launchpad, then terminal)

### 5a. Idle state (the existing launchpad, refined)
Keep the card. Changes:
- Title: "Start a 10-K audit". Subtitle: "Enter a Central Index Key to fetch the latest 10-K from SEC EDGAR."
- CIK input with live validation: digits only, auto zero-pad to 10, inline error "CIK must be 1 to 10 digits".
- "Inject deliberate anomaly (demo)" dropdown, options: None (clean audit), Scale error (1000x), Sign flip, Wrong column (prior year), Missing digit, Swapped assets and liabilities.
- Presets as 5 compact tiles with monogram, company name, CIK and a one-line hint (Apple: clean baseline. NVIDIA: scale anomaly. Microsoft: sign flip. Coca-Cola: derived liabilities. Netflix: segment check). Clicking fills the CIK and dropdown but does NOT start the run.
- Primary button: **Start audit** (no arrow).
- New panel under the card, **"Unfinished audits"**: list of threads with status `Waiting for review` or `Interrupted` (from `GET /api/v1/runs`), each with a **Resume** button. This is how the auditor continues a paused run after closing the browser.

### 5b. Run state: the terminal appears
When the auditor presses Start audit, the launchpad card collapses to a one-line summary (`CIK 0000320193 · Apple Inc. · FY2023 · anomaly: none`) and the terminal opens below it. See section 6 for the terminal component.

**Terminal title:** `Extraction Agent`. Script (this is the exact content to print, with real values from the API; the sample below is Apple FY2023, USD millions):

```
[14:02:01] INFO   thread created: run_2023_320193_401a3f
[14:02:01] STEP   fetch_filing
[14:02:01] CALL   GET data.sec.gov/submissions/CIK0000320193.json
[14:02:02] OK     200 · 10-K accession 0000320193-23-000106 · filed 2023-11-03
[14:02:02] CACHE  saved to data/cache/ (next run will not hit SEC)
[14:02:02] STEP   extract_metrics
[14:02:03] FOUND  revenue               383,285   tag: RevenueFromContractWithCustomerExcludingAssessedTax
[14:02:03]        source: "Total net sales  383,285  394,328  365,817"
[14:02:03] FOUND  net_income             96,995   tag: NetIncomeLoss
[14:02:03] FOUND  total_assets          352,583   tag: Assets
[14:02:03] FOUND  total_liabilities     290,437   tag: Liabilities
[14:02:03] FOUND  shareholders_equity    62,146   tag: StockholdersEquity
[14:02:04] FOUND  operating_cash_flow   110,543   tag: NetCashProvidedByUsedInOperatingActivities
[14:02:04] FOUND  current_assets        143,566
[14:02:04] FOUND  current_liabilities   145,308
[14:02:04] FOUND  cash_and_equivalents   29,965
[14:02:04] FOUND  diluted_eps               6.13
[14:02:04] STEP   score_confidence (grounding 0.40 · label 0.25 · unit 0.20 · yoy 0.15)
[14:02:05] SCORE  revenue 1.00 · net_income 1.00 · total_assets 1.00 · ...
[14:02:05] CHECKPOINT CP-01 saved  hash a91f…c3d2
[14:02:05] DONE   10 of 10 metrics extracted · 0 missing · 0 below threshold 0.85
```

Rules: values must come from the backend event stream, never hard-coded in Live API mode. If a value is missing, print `MISS` in red with the reason; never invent a number.

When the terminal prints `DONE`, wait 1.2 seconds, mark Step 1 as Passed, and **auto-advance to Step 2**.

---

## 6. The terminal component (reuse for Steps 1, 3, 4, 5)

One reusable component: `<AgentTerminal agent="asset_auditing" />`.

**Look:** `--term-bg` background, 10px radius, mono 13px, 1.55 line height. A thin header bar with three muted dots (no traffic-light colours), the agent name, a status chip, and right-aligned controls.

**Header controls:** Pause/Play stream, Speed (1x, 2x, 4x), Copy log, Download log (.txt), Clear is NOT offered (audit principle: nothing is deleted).

**Line anatomy:** `[HH:MM:SS]  LEVEL  message`. Level tags are fixed-width and coloured:

| Level | Colour | Meaning |
| :--- | :--- | :--- |
| INFO / STEP | `--info` | Progress |
| CALL / OK | `--term-ink` / `--pass` | External requests and results |
| INPUT | white | Exact data the agent is using |
| CALC | `#7FD1C7` | A calculation, shown with the numbers substituted |
| PASS | `--pass` | Check passed |
| WARN | `--warn` | Soft flag |
| FAIL | `--fail` | Hard failure |
| HUMAN | `--human` | Escalated to a reviewer |
| RESUME | `--info` | Resumed from a checkpoint |
| CHECKPOINT | teal bold | State saved |

**Behaviour:**
- Lines appear one at a time (typewriter on the message at about 400 characters per second at 1x; instant when "Pitch Demo" is paused or speed is 4x). Auto-scroll to bottom, but stop auto-scroll if the user scrolls up and show a "Jump to latest" pill.
- `CALC` lines show the full expression and the result, for example `CALC  143,566 + 209,017 = 352,583 (reported 352,583)  Δ 0`.
- A **blinking block cursor** at the end only while running.
- Accessibility: container has `role="log"` and `aria-live="polite"`; respects `prefers-reduced-motion` by printing lines instantly with no cursor blink.
- Data source: Live API mode reads Server-Sent Events from `GET /api/v1/runs/{run_id}/events`. Pitch Demo mode replays a scripted JSON timeline from `dashboard/demo_timeline.json`. Both feed the same component with the same event shape (section 11).

---

## 7. Step 2: Extraction output

Layout: a full-width table card plus a right-hand evidence panel.

**Table columns:** Metric · Value (USD millions, right aligned, mono) · Period · XBRL tag · Confidence (bar plus number) · Status badge.

- Confidence cell: a 60px bar coloured by threshold (green at or above threshold, amber within 0.05 below, red below), with the number beside it, for example `0.97`.
- Status badge: `Auto-approved` (green), `Needs review` (purple), `Derived` (grey, for derived values such as total liabilities computed as liabilities-and-equity minus equity).
- Clicking a row selects it and fills the **Evidence panel** on the right: the original source snippet with the extracted number highlighted in yellow, the source URL, the 4-factor confidence breakdown (grounding, label, unit, year-on-year, each as a small bar with weight), and the XBRL tag.
- Above the table, four counters: Metrics extracted, High confidence, Low confidence, Missing. The threshold is displayed as "Review threshold 0.85 (configurable in Settings)".
- A footer button **Run Asset Auditing Agent** (primary). The flow also auto-advances after 2 seconds in Pitch Demo mode, but never skips a Needs-review pause.

---

## 8. Steps 3, 4, 5: the three audit agents

All three share one **Agent screen template**. This is the main thing to build well.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ Asset Auditing Agent        [status chip]        Checkpoint CP-02        │
│ Verifies the accounting equation and sub-totals                           │
├───────────────────────┬──────────────────────────────────────────────────┤
│ INPUT                 │ TERMINAL                                          │
│ (data the agent uses) │ (live, section 6)                                 │
│ table of exact values │                                                   │
├───────────────────────┴──────────────────────────────────────────────────┤
│ OUTPUT  — checks list (pass/fail), flags, final result, confidence impact │
├──────────────────────────────────────────────────────────────────────────┤
│ [Back]                                       [Continue to next agent]     │
└──────────────────────────────────────────────────────────────────────────┘
```

- **Input pane (left, 340px):** a compact two-column table of the exact metric values this agent receives, each with its source tag. This answers the auditor's question "what data are you using?".
- **Terminal (right):** shows processing with `CALC` lines.
- **Output card (bottom):** appears only after the terminal finishes. Contains: a list of checks (icon, name, formula, result), any flags with severity, the agent's final output sentence, and the effect on confidence ("Hard failure: all balance sheet metrics forced to Needs review").
- **Checkpoint:** when the agent finishes with all checks passed, print `CHECKPOINT CP-0n saved`, update the checkpoint bar, mark the step Passed, and enable "Continue". Agents run **one at a time, in order**; the next agent starts automatically 1.5 seconds after the previous one passes, with a visible countdown the auditor can cancel.

### 8.1 Asset Auditing Agent (Step 3)
Checks (from the 7-step engine): current assets + noncurrent assets = total assets; current liabilities + noncurrent liabilities = total liabilities; liabilities + equity = liabilities and equity; total assets = liabilities + equity.

Sample terminal (Apple FY2023):
```
[14:02:07] STEP   asset_auditing started (resumed: no)
[14:02:07] INPUT  total_assets 352,583 · total_liabilities 290,437 · equity 62,146
[14:02:07] CALC   F1  143,566 + 209,017 = 352,583   reported 352,583   Δ 0   tolerance 0.5
[14:02:07] PASS   F1 current + noncurrent assets = total assets
[14:02:08] CALC   F2  145,308 + 145,129 = 290,437   reported 290,437   Δ 0
[14:02:08] PASS   F2 liabilities sub-totals
[14:02:08] CALC   F4  290,437 + 62,146 = 352,583   total assets 352,583   Δ 0
[14:02:08] PASS   F4 accounting equation: assets = liabilities + equity
[14:02:08] CHECKPOINT CP-02 saved  hash 7be0…19aa
[14:02:08] DONE   4 of 4 checks passed · 0 flags
```

**Failure example** (Scale anomaly injected into liabilities):
```
[14:02:08] CALC   F4  290,437,000 + 62,146 = 290,499,146   total assets 352,583   Δ 290,146,563
[14:02:08] FAIL   F4 accounting equation does not hold
[14:02:08] WARN   scale error likely: 290,437,000 / 290,437 = 1000.0x
[14:02:08] HUMAN  escalated: total_liabilities (hard failure forces review of all balance sheet metrics)
[14:02:08] CHECKPOINT CP-02 saved (state: WAITING_FOR_REVIEW)
[14:02:08] PAUSE  workflow paused. Nothing after this point has run.
```

### 8.2 Segment Validation Agent (Step 4)
Check: sum of segment revenues + corporate/other minus intersegment eliminations = consolidated revenue. If the company has one segment, show `N/A: single reportable segment` as a grey **Skipped (not applicable)** result (not a pass, not a fail).

Sample (Apple FY2023, geographic segments):
```
[14:02:10] STEP   segment_validation started
[14:02:10] INPUT  Americas 162,560 · Europe 94,294 · Greater China 72,559 · Japan 24,257 · Rest of Asia Pacific 29,615
[14:02:10] CALC   162,560 + 94,294 + 72,559 + 24,257 + 29,615 = 383,285
[14:02:10] CALC   consolidated revenue (from Extraction) = 383,285   Δ 0
[14:02:11] PASS   segment totals reconcile to consolidated revenue
[14:02:11] CHECKPOINT CP-03 saved  hash c40d…77e1
```
The Output card shows a small stacked bar of segment shares next to the check, with each segment value in the table.

### 8.3 YoY Comparing Agent (Step 5)
Compares current vs prior period (and 3+ periods when available, using Z-score and MAD). Detects: change above metric threshold, 1000x scale mismatch, sign flip, "approaching threshold" at 75% of threshold.

Input pane here is a table: Metric · FY2023 · FY2022 · Threshold.
Sample:
```
[14:02:12] STEP   yoy_comparison started
[14:02:12] CALC   revenue            (383,285 - 394,328) / 394,328 = -2.80%   threshold 50%   PASS
[14:02:12] CALC   net_income         (96,995 - 99,803) / 99,803 = -2.81%      threshold 150%  PASS
[14:02:12] CALC   total_assets       (352,583 - 352,755) / 352,755 = -0.05%   threshold 30%   PASS
[14:02:12] CALC   total_liabilities  (290,437 - 302,083) / 302,083 = -3.86%   threshold 40%   PASS
[14:02:13] CALC   shareholders_equity (62,146 - 50,672) / 50,672 = +22.64%    threshold 30%   WARN
[14:02:13] WARN   shareholders_equity change is 75.5% of threshold (approaching)
[14:02:13] PASS   no sign flips · no scale mismatch
[14:02:13] INFO   explanation: equity rose while liabilities fell, consistent with reduced debt and lower buybacks relative to earnings.
[14:02:13] CHECKPOINT CP-04 saved  hash 2d9c…b0f8
```
A soft WARN does not pause the workflow; it adds a flag to the output card. Only a **hard** failure, or confidence below threshold, pauses for human review. The natural-language explanation comes from the existing `YOY_model/explain.py`, and must be labelled "Generated explanation".

(Values above are illustrative; the UI must show whatever the backend streams.)

---

## 9. Checkpoints, human review, and resume (the core behaviour)

### 9.1 Checkpoint rules
- Every agent finishes by saving a checkpoint, shown as `CP-0n`, a short hash, and a UTC timestamp, written to the checkpoint bar and to the terminal.
- If any check at a checkpoint fails (hard failure) or any metric stays below the review threshold, the workflow **pauses at that checkpoint**. The step turns purple, the thread chip says "Waiting for review", and the Human Review drawer opens automatically.
- The UI must never advance past a paused checkpoint.

### 9.2 Human Review drawer (right side, 480px, over the current step)
Contents, top to bottom:
1. Header: "Review required" · metric name · company · period.
2. Extracted value (large, mono) with unit, and the confidence score with its 4-factor breakdown.
3. **Reason for escalation** in plain language (for example: "Accounting equation failed by 290,146,563. Total liabilities appears to be 1000x too large.").
4. **Original source snippet** with the number highlighted, plus a link to the filing.
5. Validation checks affecting this metric with pass/fail icons.
6. Decision area: three buttons, **Approve**, **Correct**, **Reject**. Correct reveals a number field (and shows the difference from the original). Reject requires a comment.
7. Reviewer name (required) and comment.
8. Submit button: **Record decision and resume**. It opens a confirm line: "This will be written permanently to the audit log."

If several metrics are flagged, the drawer has a queue (2 of 4) with Previous / Next; Submit is enabled per item, and the workflow resumes when the last pending item is decided.

### 9.3 Resume behaviour (must be visible)
After a decision, the terminal of the paused agent prints:
```
[14:09:41] HUMAN  decision recorded: CORRECT total_liabilities 290,437,000 → 290,437 by A. Rao
[14:09:41] AUDIT  line 7 appended · hash 5f12…e9a0
[14:09:41] RESUME resuming thread run_2023_320193_401a3f from CP-02
[14:09:41] SKIP   fetch_filing (done, cached)
[14:09:41] SKIP   extract_metrics (done, cached)
[14:09:42] CALC   F4  290,437 + 62,146 = 352,583   Δ 0
[14:09:42] PASS   F4 accounting equation: assets = liabilities + equity
[14:09:42] CHECKPOINT CP-02 saved (state: RESOLVED)
```
Rules:
- Completed steps show `SKIP … (done)`, proving nothing is repeated.
- The step shows the Resumed badge; completed earlier steps keep their original logs.
- **Page reload or closing the browser:** on load, the UI reads `GET /api/v1/runs/{run_id}` (from the URL hash `#run=<id>` or the Unfinished audits panel) and rehydrates: stepper, checkpoint bar, terminal logs so far, and the open review drawer. It resumes at the last checkpoint, never at step 1.
- Submitting the same decision twice shows a calm inline message ("This item has already been decided") and does not create a second audit line (backend returns HTTP 409).
- **Rejected value:** the item is excluded from the final report; the stepper shows red for that agent's output card but the workflow continues to the next agent.

---

## 10. Steps 6 and 7

### Step 6: Final verification
Title: "Final verification against SEC Company Facts". A terminal (short) prints the comparison calls, then the report appears:
- Headline strip, five figures: Extraction accuracy (%), Auto-approved (n), Escalated (n), Escalation precision (%), Missed errors (n). Missed errors is shown in red if above zero, with the list underneath.
- Table: Metric · Extracted · SEC Company Facts · Difference · Match (tolerance 0.5%). Corrected values show a purple "Corrected by reviewer" tag and are compared as corrected.
- Small bar chart of accuracy per metric.
- Notice line: "Ground truth was not used in any approval decision."
- Buttons: Download CSV, Download JSON, **Go to audit history**.

### Step 7: Audit history
- Header badge with a lock icon: "Append-only log". A second badge: **Chain verified** (green) or **Tampering detected at line N** (red), from `GET /api/v1/audit-log/verify`.
- Table (newest first): # · Timestamp (UTC) · Run · Metric · Original value · Corrected value · Decision (Approve/Correct/Reject badge) · Reviewer · Comment · Hash (first 8 chars, copy on click).
- Filters: by run, decision, reviewer. Search box.
- There are **no** edit, delete, or clear buttons anywhere. Export CSV only.
- Row click opens a read-only detail with prev_hash and hash, so the chain is visible.

---

## 11. Event contract (agree with the backend owner before coding)

Each terminal line is one event:

```json
{
  "seq": 42,
  "ts": "2026-11-13T14:02:08Z",
  "run_id": "run_2023_320193_401a3f",
  "agent": "asset_auditing",
  "level": "CALC",
  "message": "F4  290,437 + 62,146 = 352,583  total assets 352,583  Δ 0",
  "data": { "check": "accounting_equation", "passed": true },
  "checkpoint": null
}
```
- `level` is one of: INFO, STEP, CALL, OK, INPUT, CALC, PASS, WARN, FAIL, HUMAN, RESUME, SKIP, AUDIT, CHECKPOINT, DONE, PAUSE.
- `seq` is strictly increasing per run so the UI can reconnect and ask for events after a given `seq` without duplicates.
- Additional endpoints needed beyond the existing contract: `GET /api/v1/runs/{run_id}/events?after=<seq>` (Server-Sent Events), and `GET /api/v1/runs` returning status for the Unfinished audits panel.

---

## 12. Copy and tone

- Formal, plain, active voice. Sentence case. No exclamation marks, no emoji.
- Buttons name the action: **Start audit**, **Approve**, **Correct value**, **Reject**, **Record decision and resume**, **Download CSV**.
- Errors say what happened and what to do: "SEC EDGAR did not respond (HTTP 429). Retrying in 10 seconds. You can also switch to Pitch Demo mode."
- Empty states are invitations: "No audits yet. Enter a CIK or choose a preset to start."
- Always show units ("USD millions") next to numbers in table headers.

---

## 13. Motion

Motion only where it explains something:
- Terminal typewriter and cursor (while running).
- The running step's ring spinner.
- Drawer slide-in (200ms) when a human review opens; it should feel like the workflow "stopping".
- A one-time highlight fade on the checkpoint bar when a new checkpoint saves.
Nothing else animates. Honour `prefers-reduced-motion`.

---

## 14. Responsive and accessibility

- Desktop first (1280px). At 900px and below, the agent screen stacks: Input, then Terminal, then Output. The drawer becomes a full-screen sheet on mobile.
- Visible keyboard focus ring (2px `--primary`). All controls reachable by Tab. Drawer traps focus and closes with Esc only if no unsaved decision exists.
- Contrast: text at least 4.5:1 on its background, including terminal colours (check `--fail` and `--human` on `--term-bg`; lighten them in the terminal if needed).

---

## 15. Build order for the AI (do one phase per request, run and check after each)

1. **Shell:** rename to V.A.U.L.T, apply tokens, new 7-step stepper, checkpoint bar, Mode toggle kept. Show screenshot.
2. **Terminal component** and the event player. Drive it from `dashboard/demo_timeline.json` first (Pitch Demo mode) so it works without a backend.
3. **Step 1 and Step 2** (launchpad, extraction terminal, output table with evidence panel).
4. **Agent template** and the three agents (Steps 3, 4, 5) using scripted timelines, including one failing timeline.
5. **Human Review drawer, pause, resume,** and page-reload rehydration.
6. **Step 6 and Step 7** (accuracy report, audit history with chain badge).
7. **Live API mode:** connect to the backend events and endpoints; keep Pitch Demo working as the offline fallback.

---

## 16. Acceptance checklist

- [ ] Name is V.A.U.L.T everywhere; no "SATYA" or "Tri-Agent" remains (search the codebase).
- [ ] Entering a CIK opens the extraction terminal and shows every extracted metric with its tag and source.
- [ ] Step 2 shows the table, confidence bars, and source evidence with the number highlighted.
- [ ] Asset Auditing, Segment Validation and YoY Comparing each run one after another in their own terminal, with Input, Process and Output visible.
- [ ] Each agent saves a checkpoint; the checkpoint bar updates.
- [ ] A failed checkpoint pauses the workflow and opens the review drawer; nothing runs after it.
- [ ] Approve, Correct and Reject all work; the decision is written to the audit log before the workflow resumes.
- [ ] After resume, completed steps print SKIP and are not repeated.
- [ ] Refreshing the browser while paused restores the exact state and the open review.
- [ ] Final verification shows accuracy, auto-approved vs escalated counts, escalation precision and missed errors.
- [ ] Audit history is read-only, newest first, with hash chain verification.
- [ ] Works fully in Pitch Demo mode with Wi-Fi off.
