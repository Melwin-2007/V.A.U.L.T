"""Cryptographically hash-chained, append-only audit logger for human decisions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_AUDIT_LOG_PATH = Path("audit_logs/decisions.jsonl")
GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"

_LOG_LOCK = Lock()


def calculate_hash(
    prev_hash: str,
    audit_id: str,
    run_id: str,
    metric_id: str,
    decision: str,
    corrected_value: Optional[float],
    reviewer: str,
    timestamp_utc: str,
) -> str:
    """Compute the SHA-256 fingerprint for an audit line."""
    payload = f"{prev_hash}{audit_id}{run_id}{metric_id}{decision}{str(corrected_value)}{reviewer}{timestamp_utc}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLogger:
    """Thread-safe, append-only audit logger enforcing cryptographic chain integrity."""

    def __init__(self, log_path: Path = DEFAULT_AUDIT_LOG_PATH) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_last_record(self) -> Optional[Dict[str, Any]]:
        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            return None
        last_line = ""
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last_line = line
        if not last_line:
            return None
        try:
            return json.loads(last_line)
        except Exception:
            return None

    def log_decision(
        self,
        run_id: str,
        metric_id: str,
        decision: str,
        original_value: Optional[float],
        corrected_value: Optional[float] = None,
        reviewer: str = "Human Auditor",
        comment: str = "",
        timestamp_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append a decision record to the hash chain.

        Idempotent: if an identical (run_id, metric_id, decision) entry already
        exists in the log, returns the existing record without appending.
        """
        norm_decision = decision.strip().lower()
        if norm_decision not in ("approve", "correct", "reject"):
            raise ValueError(f"Invalid audit decision: {decision!r}")

        with _LOG_LOCK:
            # Idempotency check: avoid duplicate log entries for repeat resumes
            existing = self.get_records(run_id=run_id)
            for r in existing:
                if r.get("metric_id") == metric_id and r.get("decision") == norm_decision:
                    return r

            last_rec = self._get_last_record()
            prev_hash = last_rec["hash"] if last_rec and "hash" in last_rec else GENESIS_HASH

            ts = timestamp_utc or datetime.now(timezone.utc).isoformat()
            count = len(self.get_records()) + 1
            audit_id = f"aud_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{count:05d}"

            record_hash = calculate_hash(
                prev_hash=prev_hash,
                audit_id=audit_id,
                run_id=run_id,
                metric_id=metric_id,
                decision=norm_decision,
                corrected_value=corrected_value,
                reviewer=reviewer,
                timestamp_utc=ts,
            )

            record = {
                "audit_id": audit_id,
                "run_id": run_id,
                "metric_id": metric_id,
                "original_value": original_value,
                "corrected_value": corrected_value,
                "decision": norm_decision,
                "reviewer": reviewer,
                "comment": comment,
                "timestamp_utc": ts,
                "prev_hash": prev_hash,
                "hash": record_hash,
            }

            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

            return record

    def get_records(self, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Read all logged decisions, optionally filtered by run_id."""
        if not self.log_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        with open(self.log_path, "r", encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if run_id is None or data.get("run_id") == run_id:
                        records.append(data)
                except Exception:
                    continue
        return records

    def verify_chain(self) -> Tuple[bool, int, Optional[int]]:
        """Verify the integrity of the cryptographic hash chain.

        Returns:
            (verified, total_records, broken_index)
            If broken, broken_index indicates the 0-based index of the tampered record.
        """
        records = self.get_records()
        if not records:
            return True, 0, None

        expected_prev = GENESIS_HASH
        for idx, rec in enumerate(records):
            actual_prev = rec.get("prev_hash")
            if actual_prev != expected_prev:
                return False, len(records), idx

            expected_hash = calculate_hash(
                prev_hash=expected_prev,
                audit_id=rec.get("audit_id", ""),
                run_id=rec.get("run_id", ""),
                metric_id=rec.get("metric_id", ""),
                decision=rec.get("decision", ""),
                corrected_value=rec.get("corrected_value"),
                reviewer=rec.get("reviewer", ""),
                timestamp_utc=rec.get("timestamp_utc", ""),
            )

            if rec.get("hash") != expected_hash:
                return False, len(records), idx

            expected_prev = rec.get("hash", "")

        return True, len(records), None


_GLOBAL_AUDIT_LOGGER = AuditLogger()


def log_decision(
    run_id: str,
    metric_id: str,
    decision: str,
    original_value: Optional[float],
    corrected_value: Optional[float] = None,
    reviewer: str = "Human Auditor",
    comment: str = "",
) -> Dict[str, Any]:
    return _GLOBAL_AUDIT_LOGGER.log_decision(
        run_id=run_id,
        metric_id=metric_id,
        decision=decision,
        original_value=original_value,
        corrected_value=corrected_value,
        reviewer=reviewer,
        comment=comment,
    )


def get_audit_records(run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _GLOBAL_AUDIT_LOGGER.get_records(run_id=run_id)


def verify_chain() -> Dict[str, Any]:
    verified, total, broken_idx = _GLOBAL_AUDIT_LOGGER.verify_chain()
    return {
        "verified": verified,
        "total_records": total,
        "broken_index": broken_idx,
        "status": "VALID" if verified else "TAMPERING_DETECTED",
    }
