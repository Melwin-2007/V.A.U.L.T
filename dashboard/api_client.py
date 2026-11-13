"""Client module interacting with V.A.U.L.T FastAPI backend or mock fixtures."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

MOCK_FILE_PATH = Path(__file__).resolve().parent / "mock_data.json"
DEFAULT_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api/v1")


class VaultApiClient:
    """Client for V.A.U.L.T Auditor Agent REST API with mock fallback."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        use_mock: Optional[bool] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        if use_mock is not None:
            self.use_mock = use_mock
        else:
            self.use_mock = os.getenv("USE_MOCK", "false").strip().lower() in ("true", "1", "yes")

    def _load_mock_data(self) -> Dict[str, Any]:
        if not MOCK_FILE_PATH.exists():
            return {"runs": []}
        return json.loads(MOCK_FILE_PATH.read_text(encoding="utf-8"))

    def create_run(
        self,
        cik: str,
        fiscal_year: int = 2023,
        inject_error: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self.use_mock:
            mocks = self._load_mock_data()
            return mocks["runs"][0] if mocks.get("runs") else {"run_id": "mock_run", "status": "WAITING_FOR_REVIEW"}

        resp = httpx.post(
            f"{self.base_url}/runs",
            json={"cik": cik, "fiscal_year": fiscal_year, "inject_error": inject_error},
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json()

    def list_runs(self) -> List[Dict[str, Any]]:
        if self.use_mock:
            return self._load_mock_data().get("runs", [])

        resp = httpx.get(f"{self.base_url}/runs", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_run(self, run_id: str) -> Dict[str, Any]:
        if self.use_mock:
            for r in self._load_mock_data().get("runs", []):
                if r.get("run_id") == run_id:
                    return r
            return self._load_mock_data().get("runs", [])[0]

        resp = httpx.get(f"{self.base_url}/runs/{run_id}", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_reviews(
        self,
        status: str = "pending",
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if self.use_mock:
            results = []
            for r in self._load_mock_data().get("runs", []):
                if run_id and r.get("run_id") != run_id:
                    continue
                for m in r.get("metrics", []):
                    m_status = m.get("status")
                    if status == "pending" and m_status in ("NEEDS_REVIEW", "WAITING_FOR_REVIEW"):
                        results.append(m)
                    elif status == "completed" and m_status in ("APPROVED", "CORRECTED", "REJECTED"):
                        results.append(m)
                    elif status == "all":
                        results.append(m)
            return results

        params = {"status": status}
        if run_id:
            params["run_id"] = run_id
        resp = httpx.get(f"{self.base_url}/reviews", params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def submit_review(
        self,
        run_id: str,
        metric_id: str,
        decision: str,
        corrected_value: Optional[float] = None,
        reviewer: str = "Human Auditor",
        comment: str = "",
    ) -> Dict[str, Any]:
        if self.use_mock:
            return {
                "status": "ok",
                "run_id": run_id,
                "metric_id": metric_id,
                "decision": decision,
                "remaining_reviews": 0,
            }

        resp = httpx.post(
            f"{self.base_url}/runs/{run_id}/review",
            json={
                "metric_id": metric_id,
                "decision": decision,
                "corrected_value": corrected_value,
                "reviewer": reviewer,
                "comment": comment,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()

    def get_accuracy_report(self, run_id: str) -> Dict[str, Any]:
        if self.use_mock:
            return {
                "cik": "0000320193",
                "company": "Apple Inc.",
                "fiscal_year": 2023,
                "total_metrics": 10,
                "matched_metrics": 9,
                "overall_accuracy_pct": 90.0,
                "auto_approved_count": 7,
                "escalated_count": 3,
                "escalation_precision_pct": 33.33,
                "missed_errors": [],
            }

        resp = httpx.get(f"{self.base_url}/runs/{run_id}/accuracy-report", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_audit_log(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.use_mock:
            return [
                {
                    "audit_id": "aud_mock_001",
                    "run_id": run_id or "run_mock",
                    "metric_id": "shareholders_equity",
                    "original_value": 72146000000.0,
                    "corrected_value": 62146000000.0,
                    "decision": "correct",
                    "reviewer": "Alex Morgan (CPA)",
                    "comment": "Corrected typo against SEC 10-K Page 64.",
                    "timestamp_utc": "2026-11-13T02:00:00Z",
                    "prev_hash": "0000000000000000000000000000000000000000000000000000000000000000",
                    "hash": "4a5b6c7d8e9f0123456789abcdef0123456789abcdef0123456789abcdef0123",
                }
            ]

        params = {"run_id": run_id} if run_id else {}
        resp = httpx.get(f"{self.base_url}/audit-log", params=params, timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def verify_audit_log(self) -> Dict[str, Any]:
        if self.use_mock:
            return {"verified": True, "total_records": 1, "broken_index": None, "status": "VALID"}

        resp = httpx.get(f"{self.base_url}/audit-log/verify", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def get_thresholds(self) -> Dict[str, Any]:
        if self.use_mock:
            return {"confidence_threshold": 0.85, "auto_approve_enabled": True}

        resp = httpx.get(f"{self.base_url}/config/thresholds", timeout=10.0)
        resp.raise_for_status()
        return resp.json()

    def update_thresholds(self, config_updates: Dict[str, Any]) -> Dict[str, Any]:
        if self.use_mock:
            return {"status": "updated", "config": config_updates}

        resp = httpx.put(
            f"{self.base_url}/config/thresholds",
            json=config_updates,
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()

