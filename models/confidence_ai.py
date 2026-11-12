"""
Balance Sheet Validation AI Model (Scikit-Learn + 7-Step Accounting Engine).

Trains a calibrated Random Forest AI model on all real SEC filings in the repository
plus fault-injected scenarios covering every diagnostic category:
  - CLEAN (PASS)
  - DERIVED_OR_SUBTOTAL_WARN (WARN)
  - INCOMPLETE_SKIPPED (SKIPPED)
  - WRONG_COLUMN (prior-year figure picked)
  - MISSING_NCI_MEZZANINE (omitted minority / redeemable equity)
  - WRONG_ROW (matched another balance sheet line)
  - SCALE_ERROR (x1000 or /1000)
  - SIGN_ERROR (sign flipped)
  - AMBIGUOUS_OR_MULTI_ERROR (multiple errors or unisolated imbalance)

Given ONLY a Central Index Key (CIK), fetches the company's balance sheet from the
repository, runs Steps 1-7 of the Accounting Equation Engine, computes the AI model
confidence & root-cause prediction, and logs to an append-only audit log if requested.
"""

from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import GroupKFold

from models.accounting_equation import (
    AccountingEquationEngine,
    BalanceSheetPeriodInput,
    CheckStatus,
    PeriodValidationResult,
    TwoYearValidationResult,
)
from models.cik_data_loader import CompanyBalanceSheetDataset, RepoCIKDataLoader


FEATURE_NAMES: List[str] = [
    "has_assets",
    "has_liabilities",
    "has_equity",
    "has_liab_and_equity",
    "has_nci",
    "has_mezzanine",
    "liabilities_derived",
    "eq_available",
    "eq_pass",
    "eq_log_tol_ratio",
    "f7_status_code",
    "f8_status_code",
    "p_pass",
    "q_pass",
    "prior_available",
    "prior_pass",
    "has_wrong_column",
    "has_missing_nci",
    "has_wrong_row",
    "has_scale_error",
    "has_sign_error",
    "num_hypotheses",
    "single_blamed_metric",
]


def _safe_log_tol_ratio(gap: Optional[float], tolerance: float) -> float:
    if gap is None:
        return 0.0
    ratio = abs(gap) / max(tolerance, 1.0)
    return float(min(25.0, math.log1p(ratio)))


def extract_features(
    cur: PeriodValidationResult,
    pri: Optional[PeriodValidationResult] = None,
) -> np.ndarray:
    """Convert Step 1-6 validation results into a 23-dim scale-invariant feature vector."""
    inp = cur.normalized_inputs
    checks_by_id = {c.formula_id: c for c in cur.checks}

    eq = (
        checks_by_id.get("F3")
        or checks_by_id.get("F2")
        or checks_by_id.get("F1")
    )
    f7 = checks_by_id.get("F7")
    f8 = checks_by_id.get("F8")

    def _sub_code(chk) -> float:
        if chk is None or chk.status == CheckStatus.SKIPPED:
            return 0.0
        return 1.0 if chk.status == CheckStatus.PASS else -1.0

    p_pass = 0.5
    q_pass = 0.5
    if cur.triangulation is not None:
        if cur.triangulation.p_passed is True:
            p_pass = 1.0
        elif cur.triangulation.p_passed is False:
            p_pass = 0.0
        if cur.triangulation.q_passed is True:
            q_pass = 1.0
        elif cur.triangulation.q_passed is False:
            q_pass = 0.0
    elif eq is not None and eq.status != CheckStatus.SKIPPED and not cur.liabilities_derived:
        p_pass = 1.0 if eq.status == CheckStatus.PASS else 0.5
        q_pass = 1.0 if eq.status == CheckStatus.PASS else 0.5

    prior_avail = 1.0 if pri is not None else 0.0
    if pri is None:
        prior_pass = 0.5
    elif pri.status == CheckStatus.PASS:
        prior_pass = 1.0
    elif pri.status == CheckStatus.FAIL:
        prior_pass = 0.0
    else:
        prior_pass = 0.5

    hyp_types = {h.hypothesis_type for h in cur.hypotheses}

    vec = [
        1.0 if inp.get("assets") is not None else 0.0,
        1.0 if inp.get("liabilities") is not None else 0.0,
        1.0 if (inp.get("equity") is not None or inp.get("equity_with_nci") is not None) else 0.0,
        1.0 if inp.get("liabilities_and_equity") is not None else 0.0,
        1.0 if (inp.get("nci") is not None and abs(inp["nci"]) > 0) else 0.0,
        1.0 if (inp.get("mezzanine_equity") is not None and abs(inp["mezzanine_equity"]) > 0) else 0.0,
        1.0 if cur.liabilities_derived else 0.0,
        1.0 if (eq is not None and eq.status != CheckStatus.SKIPPED) else 0.0,
        1.0 if (eq is not None and eq.status == CheckStatus.PASS) else 0.0,
        _safe_log_tol_ratio(eq.gap if eq else None, cur.tolerance),
        _sub_code(f7),
        _sub_code(f8),
        p_pass,
        q_pass,
        prior_avail,
        prior_pass,
        1.0 if "wrong_column" in hyp_types else 0.0,
        1.0 if "missing_nci_mezzanine" in hyp_types else 0.0,
        1.0 if "wrong_row" in hyp_types else 0.0,
        1.0 if "scale_error" in hyp_types else 0.0,
        1.0 if "sign_error" in hyp_types else 0.0,
        float(len(cur.hypotheses)),
        1.0 if (cur.blamed_metric is not None and not cur.is_ambiguous) else 0.0,
    ]
    return np.array(vec, dtype=np.float64)


@dataclass
class AIValidationReport:
    cik: Optional[str]
    company_name: str
    ticker: str
    form: str
    filing_date: str
    source_type: str
    source_path: str
    verdict: CheckStatus
    reason: str
    model_confidence: float  # 0.0 .. 1.0 calibrated AI confidence
    ai_predicted_verdict: str
    ai_verdict_probabilities: Dict[str, float]
    ai_predicted_root_cause: str
    ai_root_cause_probabilities: Dict[str, float]
    metric_confidences: Dict[str, float]
    validation_result: TwoYearValidationResult

    def to_dict(self) -> Dict[str, Any]:
        cur = self.validation_result.current_period
        pri = self.validation_result.prior_period
        return {
            "cik": self.cik,
            "company_name": self.company_name,
            "ticker": self.ticker,
            "form": self.form,
            "filing_date": self.filing_date,
            "source_type": self.source_type,
            "source_path": self.source_path,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "model_confidence": round(self.model_confidence, 4),
            "model_confidence_pct": round(self.model_confidence * 100.0, 2),
            "ai_predicted_verdict": self.ai_predicted_verdict,
            "ai_verdict_probabilities": {k: round(v, 4) for k, v in self.ai_verdict_probabilities.items()},
            "ai_predicted_root_cause": self.ai_predicted_root_cause,
            "ai_root_cause_probabilities": {k: round(v, 4) for k, v in self.ai_root_cause_probabilities.items()},
            "metric_confidences": self.metric_confidences,
            "two_year_pattern": self.validation_result.two_year_pattern,
            "two_year_meaning": self.validation_result.two_year_meaning,
            "action": self.validation_result.action,
            "escalated_metrics": self.validation_result.escalated_metrics,
            "current_period": {
                "period_label": cur.period_label,
                "status": cur.status.value,
                "tolerance": cur.tolerance,
                "reporting_scale": cur.reporting_scale,
                "formula_used": cur.effective_equity_formula,
                "liabilities_derived": cur.liabilities_derived,
                "normalized_inputs": cur.normalized_inputs,
                "blamed_metric": cur.blamed_metric,
                "is_ambiguous": cur.is_ambiguous,
                "suggested_fixes": cur.suggested_fixes,
                "checks": [
                    {
                        "formula_id": c.formula_id,
                        "status": c.status.value,
                        "lhs": c.lhs,
                        "rhs": c.rhs,
                        "gap": c.gap,
                        "reason": c.reason,
                    }
                    for c in cur.checks
                ],
                "steps": [
                    {
                        "step": s.step_number,
                        "name": s.step_name,
                        "status": s.status,
                        "summary": s.summary,
                    }
                    for s in cur.steps
                ],
            },
            "prior_period": (
                {
                    "period_label": pri.period_label,
                    "status": pri.status.value,
                    "reason": pri.reason,
                }
                if pri
                else None
            ),
        }

    def format_report(self) -> str:
        """Render a clear, human-readable step-by-step validation report."""
        cur = self.validation_result.current_period
        pri = self.validation_result.prior_period
        lines: List[str] = []
        sep = "=" * 78
        lines.append(sep)
        lines.append(
            f"V.A.U.L.T ACCOUNTING EQUATION VALIDATION | {self.company_name} "
            f"({self.ticker or 'N/A'}) | CIK: {self.cik or 'N/A'}"
        )
        lines.append(
            f"Source: {self.form} ({self.filing_date or 'N/A'}) [{self.source_path}]"
        )
        lines.append(sep)
        lines.append(f"FINAL VERDICT     : {self.verdict.value}")
        lines.append(f"MODEL CONFIDENCE  : {self.model_confidence * 100:.2f}% (AI verdict={self.ai_predicted_verdict}, root_cause={self.ai_predicted_root_cause})")
        lines.append(f"REASON            : {self.reason}")
        lines.append(f"ACTION            : {self.validation_result.action}")
        lines.append(f"ESCALATED METRICS : {self.validation_result.escalated_metrics or 'None'}")
        lines.append(
            "METRIC CONFIDENCE : "
            + ", ".join(f"{k}={v * 100:.1f}%" for k, v in self.metric_confidences.items())
        )
        lines.append("-" * 78)
        lines.append(f"STEP-BY-STEP COMPUTATION ({cur.period_label}):")
        for s in cur.steps:
            lines.append(f"  Step {s.step_number} [{s.status}] {s.step_name}: {s.summary}")

        lines.append("-" * 78)
        lines.append("FORMULA CHECKS:")
        for c in cur.checks:
            gap_str = f"${c.gap:,.0f}" if c.gap is not None else "N/A"
            lines.append(f"  {c.formula_id:3s} [{c.status.value:7s}] {c.expression} | gap={gap_str} | {c.reason}")

        if cur.triangulation is not None:
            t = cur.triangulation
            lines.append("-" * 78)
            lines.append(
                f"TRIANGULATION (Step 4): P(Assets==L&E)={t.p_passed}, Q(L+E==L&E)={t.q_passed} "
                f"-> suspect={t.suspect_group}"
            )

        if cur.hypotheses:
            lines.append("-" * 78)
            lines.append("DIAGNOSIS HYPOTHESES (Step 5):")
            for h in cur.hypotheses:
                lines.append(
                    f"  #{h.rank} [{h.hypothesis_type}] metric={h.metric} -> {h.matched_evidence} Suggestion: {h.suggestion}"
                )

        if cur.suggested_fixes:
            lines.append(
                "SUGGESTED FIXES   : "
                + ", ".join(f"{k}=${v:,.0f}" for k, v in cur.suggested_fixes.items())
            )

        lines.append("-" * 78)
        lines.append(
            f"TWO-YEAR SUMMARY (Step 6): {self.validation_result.two_year_pattern} "
            f"-> {self.validation_result.two_year_meaning}"
        )
        if pri is not None:
            lines.append(f"  Prior Period ({pri.period_label}): {pri.status.value} — {pri.reason}")
        lines.append(sep)
        return "\n".join(lines)


class BalanceSheetValidationAI:
    """
    AI Model combining the deterministic 7-step Accounting Equation Engine with
    trained calibrated Scikit-Learn classifiers for confidence scoring and root-cause detection.
    """

    DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "artifacts" / "balance_sheet_ai_model.joblib"
    DEFAULT_METRICS_PATH = Path(__file__).resolve().parent / "artifacts" / "model_metrics.json"
    DEFAULT_AUDIT_LOG_PATH = Path(__file__).resolve().parent / "artifacts" / "audit_log.jsonl"

    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        audit_log_path: Optional[Union[str, Path]] = None,
        repo_root: Optional[Union[str, Path]] = None,
    ) -> None:
        self.model_path = Path(model_path) if model_path else self.DEFAULT_MODEL_PATH
        self.audit_log_path = Path(audit_log_path) if audit_log_path else self.DEFAULT_AUDIT_LOG_PATH
        self.loader = RepoCIKDataLoader(repo_root=repo_root)
        self.engine = AccountingEquationEngine(audit_log_path=self.audit_log_path)

        self.verdict_clf: Optional[CalibratedClassifierCV] = None
        self.root_cause_clf: Optional[RandomForestClassifier] = None
        self.training_metrics: Dict[str, Any] = {}

        if self.model_path.exists():
            self.load_model(self.model_path)

    # ------------------------------------------------------------------
    # Synthetic & Real Training Corpus Builder (from repo filings)
    # ------------------------------------------------------------------
    def _build_training_corpus(
        self, seed: int = 42
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Build training examples directly from ALL local filings in the repository,
        augmented with realistic fault injections across all 5 error modes,
        ambiguous multi-errors, subtotal/derived warnings, and missing-input skips.
        """
        rng = random.Random(seed)
        index = self.loader.build_index()

        # Deduplicate exact paths while scanning every available filing in the repo
        seen_paths = set()
        datasets: List[CompanyBalanceSheetDataset] = []
        for rec in index:
            p_str = str(rec.path)
            if p_str in seen_paths:
                continue
            seen_paths.add(p_str)
            try:
                if rec.source_type == "xbrl_directory":
                    ds = self.loader._load_from_xbrl_dir(rec)
                else:
                    ds = self.loader._load_from_entity_json(rec)
                datasets.append(ds)
            except Exception:
                continue

        X_rows: List[np.ndarray] = []
        y_verdict: List[str] = []
        y_cause: List[str] = []
        groups: List[str] = []

        engine = AccountingEquationEngine()

        def add_sample(
            cur_in: BalanceSheetPeriodInput,
            pri_in: Optional[BalanceSheetPeriodInput],
            cik_group: str,
            cause_label: str,
        ) -> None:
            res = engine.validate_two_years(cur_in, pri_in, cik=cik_group)
            feat = extract_features(res.current_period, res.prior_period)
            X_rows.append(feat)
            y_verdict.append(res.current_period.status.value)
            y_cause.append(cause_label)
            groups.append(cik_group)

        for ds in datasets:
            cur = ds.current_period
            pri = ds.prior_period
            cik_g = ds.cik

            # 1. Ground-truth clean sample (and prior period as standalone)
            base_cause = (
                "CLEAN"
                if engine.validate_period(cur, pri).status == CheckStatus.PASS
                else "DERIVED_OR_SUBTOTAL_WARN"
            )
            add_sample(cur, pri, cik_g, base_cause)

            # Mild rounding jitter within tolerance (<= 1.5 * reporting_scale)
            rep_s = float(cur.reporting_scale or 1_000_000.0)
            if cur.assets is not None:
                c_jit = copy.deepcopy(cur)
                delta = rng.uniform(-1.4, 1.4) * rep_s
                c_jit.assets = cur.assets + delta
                add_sample(c_jit, pri, cik_g, base_cause)

            # 2. WRONG_COLUMN (replace liabilities / equity / assets with prior-year value)
            if pri is not None:
                for metric_attr in ("assets", "liabilities", "equity"):
                    cur_v = getattr(cur, metric_attr)
                    pri_v = getattr(pri, metric_attr)
                    if cur_v is not None and pri_v is not None and abs(cur_v - pri_v) > 2.5 * rep_s:
                        c_wc = copy.deepcopy(cur)
                        setattr(c_wc, metric_attr, pri_v)
                        add_sample(c_wc, pri, cik_g, "WRONG_COLUMN")

            # 3. MISSING_NCI_MEZZANINE (omit NCI or mezzanine when present)
            if cur.nci is not None and abs(cur.nci) > 2.5 * rep_s:
                c_nci = copy.deepcopy(cur)
                c_nci.nci = None
                c_nci.equity_with_nci = None
                c_nci.line_items["Noncontrolling Interest"] = cur.nci
                add_sample(c_nci, pri, cik_g, "MISSING_NCI_MEZZANINE")
            if cur.mezzanine_equity is not None and abs(cur.mezzanine_equity) > 2.5 * rep_s:
                c_mez = copy.deepcopy(cur)
                c_mez.mezzanine_equity = None
                c_mez.line_items["Redeemable Noncontrolling Interest"] = cur.mezzanine_equity
                add_sample(c_mez, pri, cik_g, "MISSING_NCI_MEZZANINE")

            # 4. WRONG_ROW (swap liabilities with current_liabilities, or assets with current_assets)
            if cur.liabilities is not None and cur.current_liabilities is not None:
                if abs(cur.liabilities - cur.current_liabilities) > 2.5 * rep_s:
                    c_wr = copy.deepcopy(cur)
                    c_wr.liabilities = cur.current_liabilities
                    c_wr.line_items.setdefault("Total Current Liabilities", cur.current_liabilities)
                    add_sample(c_wr, pri, cik_g, "WRONG_ROW")
            if cur.assets is not None and cur.current_assets is not None:
                if abs(cur.assets - cur.current_assets) > 2.5 * rep_s:
                    c_wra = copy.deepcopy(cur)
                    c_wra.assets = cur.current_assets
                    c_wra.line_items.setdefault("Total Current Assets", cur.current_assets)
                    add_sample(c_wra, pri, cik_g, "WRONG_ROW")

            # 5. SCALE_ERROR (x1000 or /1000 on one metric)
            for metric_attr in ("assets", "liabilities", "equity"):
                val = getattr(cur, metric_attr)
                if val is not None and abs(val) > 10 * rep_s:
                    for factor in (1000.0, 0.001):
                        c_sc = copy.deepcopy(cur)
                        setattr(c_sc, metric_attr, val * factor)
                        add_sample(c_sc, pri, cik_g, "SCALE_ERROR")

            # 6. SIGN_ERROR (flip sign of liabilities, equity, or assets)
            for metric_attr in ("assets", "liabilities", "equity"):
                val = getattr(cur, metric_attr)
                if val is not None and abs(val) > 5 * rep_s:
                    c_sg = copy.deepcopy(cur)
                    setattr(c_sg, metric_attr, -val)
                    add_sample(c_sg, pri, cik_g, "SIGN_ERROR")

            # 7. AMBIGUOUS_OR_MULTI_ERROR (perturb multiple metrics or L&E absent + random offset)
            if cur.assets is not None and cur.liabilities is not None:
                c_amb = copy.deepcopy(cur)
                c_amb.assets = cur.assets * rng.uniform(1.03, 1.15)
                c_amb.liabilities = cur.liabilities * rng.uniform(0.85, 0.96)
                add_sample(c_amb, pri, cik_g, "AMBIGUOUS_OR_MULTI_ERROR")

                c_amb2 = copy.deepcopy(cur)
                c_amb2.liabilities_and_equity = None
                c_amb2.liabilities = cur.liabilities + rng.uniform(5.0, 50.0) * rep_s
                add_sample(c_amb2, pri, cik_g, "AMBIGUOUS_OR_MULTI_ERROR")

            # 8. DERIVED_OR_SUBTOTAL_WARN (derived-only or F7/F8 subtotal mismatch)
            if cur.liabilities_and_equity is not None and (cur.equity is not None or cur.equity_with_nci is not None):
                c_der = copy.deepcopy(cur)
                c_der.liabilities = None
                # Derived check only (subtotal off -> WARN)
                c_der_only = copy.deepcopy(c_der)
                c_der_only.current_assets = (cur.assets or 100.0) * 0.4
                c_der_only.noncurrent_assets = (cur.assets or 100.0) * 0.45  # F7 mismatch -> WARN
                add_sample(c_der_only, pri, cik_g, "DERIVED_OR_SUBTOTAL_WARN")

            if cur.current_assets is not None and cur.noncurrent_assets is not None:
                c_sub = copy.deepcopy(cur)
                c_sub.noncurrent_assets = cur.noncurrent_assets * 0.85
                add_sample(c_sub, pri, cik_g, "DERIVED_OR_SUBTOTAL_WARN")

            # 9. INCOMPLETE_SKIPPED (missing required fields)
            c_sk1 = copy.deepcopy(cur)
            c_sk1.assets = None
            c_sk1.liabilities_and_equity = None
            add_sample(c_sk1, pri, cik_g, "INCOMPLETE_SKIPPED")

            c_sk2 = copy.deepcopy(cur)
            c_sk2.liabilities = None
            c_sk2.liabilities_and_equity = None
            add_sample(c_sk2, pri, cik_g, "INCOMPLETE_SKIPPED")

            c_sk3 = copy.deepcopy(cur)
            c_sk3.equity = None
            c_sk3.equity_with_nci = None
            c_sk3.liabilities_and_equity = None
            add_sample(c_sk3, pri, cik_g, "INCOMPLETE_SKIPPED")

        return (
            np.vstack(X_rows),
            np.array(y_verdict),
            np.array(y_cause),
            np.array(groups),
        )

    def train(self, save: bool = True, seed: int = 42) -> Dict[str, Any]:
        """Train and evaluate the AI model using CIK-grouped cross-validation."""
        X, y_verdict, y_cause, groups = self._build_training_corpus(seed=seed)

        # Evaluate out-of-sample performance grouped by CIK (no company leakage)
        n_unique_ciks = len(set(groups))
        n_splits = min(5, max(2, n_unique_ciks))
        gkf = GroupKFold(n_splits=n_splits)

        oof_verdict_preds = np.empty_like(y_verdict)
        oof_cause_preds = np.empty_like(y_cause)

        for train_idx, val_idx in gkf.split(X, y_verdict, groups):
            base_v = RandomForestClassifier(
                n_estimators=180, max_depth=10, class_weight="balanced", random_state=seed
            )
            base_v.fit(X[train_idx], y_verdict[train_idx])
            oof_verdict_preds[val_idx] = base_v.predict(X[val_idx])

            base_c = RandomForestClassifier(
                n_estimators=180, max_depth=12, class_weight="balanced", random_state=seed
            )
            base_c.fit(X[train_idx], y_cause[train_idx])
            oof_cause_preds[val_idx] = base_c.predict(X[val_idx])

        verdict_acc = float(accuracy_score(y_verdict, oof_verdict_preds))
        verdict_f1 = float(f1_score(y_verdict, oof_verdict_preds, average="macro"))
        cause_acc = float(accuracy_score(y_cause, oof_cause_preds))
        cause_f1 = float(f1_score(y_cause, oof_cause_preds, average="macro"))

        # Fit final calibrated verdict model & root-cause classifier on full corpus
        verdict_rf = RandomForestClassifier(
            n_estimators=220, max_depth=10, class_weight="balanced", random_state=seed
        )
        self.verdict_clf = CalibratedClassifierCV(verdict_rf, cv=3, method="sigmoid")
        self.verdict_clf.fit(X, y_verdict)

        self.root_cause_clf = RandomForestClassifier(
            n_estimators=220, max_depth=12, class_weight="balanced", random_state=seed
        )
        self.root_cause_clf.fit(X, y_cause)

        # Feature importances from root_cause_clf
        importances = {
            name: round(float(imp), 4)
            for name, imp in sorted(
                zip(FEATURE_NAMES, self.root_cause_clf.feature_importances_),
                key=lambda kv: kv[1],
                reverse=True,
            )
        }

        self.training_metrics = {
            "total_samples": int(len(X)),
            "unique_ciks": int(n_unique_ciks),
            "cv_folds": int(n_splits),
            "verdict_cv_accuracy": round(verdict_acc, 4),
            "verdict_cv_macro_f1": round(verdict_f1, 4),
            "root_cause_cv_accuracy": round(cause_acc, 4),
            "root_cause_cv_macro_f1": round(cause_f1, 4),
            "verdict_classes": list(self.verdict_clf.classes_),
            "root_cause_classes": list(self.root_cause_clf.classes_),
            "top_features": dict(list(importances.items())[:10]),
        }

        if save:
            self.save_model()

        return self.training_metrics

    def save_model(self) -> None:
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "verdict_clf": self.verdict_clf,
            "root_cause_clf": self.root_cause_clf,
            "feature_names": FEATURE_NAMES,
            "training_metrics": self.training_metrics,
        }
        joblib.dump(payload, self.model_path)
        self.DEFAULT_METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.DEFAULT_METRICS_PATH.write_text(
            json.dumps(self.training_metrics, indent=2), encoding="utf-8"
        )

    def load_model(self, path: Optional[Union[str, Path]] = None) -> None:
        target = Path(path) if path else self.model_path
        payload = joblib.load(target)
        self.verdict_clf = payload["verdict_clf"]
        self.root_cause_clf = payload["root_cause_clf"]
        self.training_metrics = payload.get("training_metrics", {})

    def _ensure_trained(self) -> None:
        if self.verdict_clf is None or self.root_cause_clf is None:
            if self.model_path.exists():
                self.load_model(self.model_path)
            else:
                self.train(save=True)

    # ------------------------------------------------------------------
    # Prediction & End-to-End Validation
    # ------------------------------------------------------------------
    def _score_ai(
        self, res: TwoYearValidationResult
    ) -> Tuple[str, Dict[str, float], str, Dict[str, float], float]:
        self._ensure_trained()
        assert self.verdict_clf is not None and self.root_cause_clf is not None

        feat = extract_features(res.current_period, res.prior_period).reshape(1, -1)
        v_probs_arr = self.verdict_clf.predict_proba(feat)[0]
        v_classes = list(self.verdict_clf.classes_)
        v_probs = {cls: float(p) for cls, p in zip(v_classes, v_probs_arr)}
        ai_verdict = max(v_probs.items(), key=lambda kv: kv[1])[0]

        c_probs_arr = self.root_cause_clf.predict_proba(feat)[0]
        c_classes = list(self.root_cause_clf.classes_)
        c_probs = {cls: float(p) for cls, p in zip(c_classes, c_probs_arr)}
        ai_cause = max(c_probs.items(), key=lambda kv: kv[1])[0]

        # Calibrated confidence in the deterministic verdict
        verdict_key = res.overall_status.value
        model_conf = v_probs.get(verdict_key, max(v_probs.values()))

        return ai_verdict, v_probs, ai_cause, c_probs, float(model_conf)

    def validate_dataset(
        self,
        dataset: CompanyBalanceSheetDataset,
        log_audit: bool = False,
        override_current: Optional[BalanceSheetPeriodInput] = None,
        override_prior: Optional[BalanceSheetPeriodInput] = None,
    ) -> AIValidationReport:
        """Validate a loaded CompanyBalanceSheetDataset using Steps 1-7 + AI confidence."""
        cur = override_current if override_current is not None else dataset.current_period
        pri = override_prior if override_prior is not None else dataset.prior_period

        res = self.engine.validate_two_years(
            current_period=cur,
            prior_period=pri,
            cik=dataset.cik,
            company_name=dataset.company_name,
            log_audit=False,
        )

        ai_verdict, v_probs, ai_cause, c_probs, model_conf = self._score_ai(res)

        if log_audit:
            self.engine.append_audit_log(res, ai_model_confidence=round(model_conf, 4))

        return AIValidationReport(
            cik=dataset.cik,
            company_name=dataset.company_name,
            ticker=dataset.ticker,
            form=dataset.form,
            filing_date=dataset.filing_date,
            source_type=dataset.source_type,
            source_path=dataset.source_path,
            verdict=res.overall_status,
            reason=res.reason,
            model_confidence=model_conf,
            ai_predicted_verdict=ai_verdict,
            ai_verdict_probabilities=v_probs,
            ai_predicted_root_cause=ai_cause,
            ai_root_cause_probabilities=c_probs,
            metric_confidences=res.metric_confidences,
            validation_result=res,
        )

    def validate_cik(
        self,
        cik: Union[int, str],
        prefer_form: Optional[str] = None,
        allow_network: bool = False,
        log_audit: bool = False,
    ) -> AIValidationReport:
        """
        Fetch data from the repo using ONLY the Central Index Key (CIK),
        run all 7 steps + formulas F1-F8, and return the full AIValidationReport.
        """
        dataset = self.loader.load_by_cik(
            cik=cik,
            prefer_form=prefer_form,
            allow_network=allow_network,
        )
        return self.validate_dataset(dataset, log_audit=log_audit)

