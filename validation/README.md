# Accounting Equation Validation Engine (`validation/`)

Deterministic, pure-Python validation engine for SEC 10-K/10-Q balance sheet filings in the Human-in-the-Loop Financial Filing Auditor.

- **Exact `Decimal` arithmetic** (no floats for financial sums)
- **Zero LLM calls inside checks** (fully reproducible templates and rule logic)
- **No SEC XBRL Company Facts** used as input (`companyfacts` is reserved strictly for final accuracy reporting)
- **Human-in-the-loop**: never auto-corrects values; only suggests candidate corrections and escalates uncertain/guilty metrics

## Formulas & Execution Order

Defined in [`validation/config.yaml`](config.yaml) and executed in strict order per period (`current` and `prior`):

1. **F6 (Conditional Derivation)**: If `L` is omitted (Amazon/Disney/Coca-Cola pattern), derive `L = LE - E_total - TE` and mark `L` as `derived = true`.
2. **F4**: `A = LE` — run first whenever "Total liabilities and equity" (`LE`) is printed.
3. **F1 / F2 / F3** (`accounting_equation`):
   - `F1`: `A = L + E` (neither NCI nor TE)
   - `F2`: `A = L + E + NCI` (noncontrolling interest reported separately)
   - `F3`: `A = L + TE + E (+ NCI)` (temporary/mezzanine equity reported)
   - *Formula variant guard*: if `F1` fails and `gap` matches NCI or TE within tolerance, records `"formula variant mismatch"` and re-runs with `F2`/`F3`.
   - *Derived-value trap*: when `L` is derived via `F6`, `F1`/`F2`/`F3` and `F5` never return a clean `PASS`; they return `WARN` (`"only derived value checked"`), relying on `F4`, `F7`, `F8`.
4. **F5**: `L + TE + E_total = LE` — verifies right-side total independently.
5. **F7**: `A = AC + ANC` — asset current/non-current subtotal check.
6. **F8**: `L = LC + LNC` — liability current/non-current subtotal check.

Tolerance is strictly scale-linked: `tolerance = tolerance_multiplier * scale` (default `2 * scale`, e.g. `$2,000,000` for statements in millions).

## Triangulation & Diagnosis (on Failure)

Computes `P` (`A = LE`) and `Q` (`L + TE + E_total = LE`):

| P | Q | Meaning | Escalation Scope |
|---|---|---|---|
| pass | pass | Formula variant / NCI / TE issue | Handled via F2/F3 switch |
| pass | fail | `A` agrees with `LE`; right side is wrong | Candidates: `L`, `E` |
| fail | pass | Right side agrees with `LE`; `A` is wrong | Blame `total_assets` |
| fail | fail | Multiple errors or wrong table/period | Escalate all (`A`, `L`, `E`) |

For candidate metrics, tests six hypotheses in configured order:
1. `wrong_column` (matches prior-year column or alternative candidate)
2. `missing_nci_or_te` (gap matches NCI / TE)
3. `wrong_row` (implied value or sum of two rows matches another balance sheet row)
4. `scale_error` (`x1000` or `/1000` equals implied value)
5. `sign_error` (`-X` equals implied value)
6. `digit_transposition` (`(gap / scale) % 9 == 0`, weak hint only — never used alone)

## Decision Table

| Check Outcome | Confidence Delta | Action |
|---|---|---|
| `PASS` touching metric | `+0.05` (cap `1.0`) | Auto-approve if above threshold (`0.75`) |
| `FAIL`, high blame | `-0.30` | **ESCALATE** blamed metric only |
| `FAIL`, ambiguous | `-0.15` | **ESCALATE** all candidate metrics |
| `FAIL`, cleared by triangulation | `0.00` | Cleared (no penalty) |
| `WARN` | `-0.05` | No forced escalation |
| `SKIPPED` | `0.00` | Recorded reason |
| `derived` (`F6`) | `-0.05` on `L` | Extra derivation penalty |

## How to Run

### CLI
```bash
python -m validation.accounting_equation --cik 320193 --year 2023
python -m validation.accounting_equation --cik 789019 --year 2024
python -m validation.accounting_equation --cik 1018724
```

### FastAPI Service
```bash
uvicorn validation.api:app --reload
# POST /validate/accounting-equation  {"cik": "320193", "fiscal_year": 2023}
```

### Tests & Error-Injection Harness
```bash
pytest tests/test_validation_accounting_equation.py -s
```

