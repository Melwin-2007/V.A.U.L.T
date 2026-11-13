"""Unit test suite for the tamper-evident cryptographic audit chain."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agent.audit import AuditLogger, GENESIS_HASH


class TestAuditChain(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "test_decisions.jsonl"
        self.logger = AuditLogger(log_path=self.log_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_empty_log_is_verified(self) -> None:
        verified, total, broken = self.logger.verify_chain()
        self.assertTrue(verified)
        self.assertEqual(total, 0)
        self.assertIsNone(broken)

    def test_single_and_multiple_records_form_valid_hash_chain(self) -> None:
        rec1 = self.logger.log_decision(
            run_id="run_001",
            metric_id="run_001:revenue",
            decision="approve",
            original_value=383_285_000_000.0,
            reviewer="Auditor 1",
            comment="Approved clean revenue.",
        )
        self.assertEqual(rec1["prev_hash"], GENESIS_HASH)

        rec2 = self.logger.log_decision(
            run_id="run_001",
            metric_id="run_001:shareholders_equity",
            decision="correct",
            original_value=72_146_000_000.0,
            corrected_value=62_146_000_000.0,
            reviewer="Auditor 2",
            comment="Fixed typo.",
        )
        self.assertEqual(rec2["prev_hash"], rec1["hash"])

        rec3 = self.logger.log_decision(
            run_id="run_002",
            metric_id="run_002:total_assets",
            decision="reject",
            original_value=999_000_000.0,
            reviewer="Auditor 1",
            comment="Ungrounded.",
        )
        self.assertEqual(rec3["prev_hash"], rec2["hash"])

        verified, total, broken = self.logger.verify_chain()
        self.assertTrue(verified)
        self.assertEqual(total, 3)
        self.assertIsNone(broken)

    def test_tampering_with_middle_record_is_detected(self) -> None:
        self.logger.log_decision("run_1", "m1", "approve", 100.0)
        self.logger.log_decision("run_1", "m2", "correct", 200.0, corrected_value=250.0)
        self.logger.log_decision("run_1", "m3", "approve", 300.0)

        # Manually tamper with record 1 (second line)
        lines = self.log_path.read_text(encoding="utf-8").strip().split("\n")
        data1 = json.loads(lines[1])
        data1["corrected_value"] = 999.0  # Unauthorized manual alteration
        lines[1] = json.dumps(data1)
        self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        verified, total, broken = self.logger.verify_chain()
        self.assertFalse(verified)
        self.assertEqual(total, 3)
        self.assertEqual(broken, 1)

    def test_logging_is_idempotent_on_repeat_calls(self) -> None:
        rec_a = self.logger.log_decision("run_1", "m1", "approve", 100.0)
        rec_b = self.logger.log_decision("run_1", "m1", "approve", 100.0)

        self.assertEqual(rec_a["audit_id"], rec_b["audit_id"])
        self.assertEqual(len(self.logger.get_records()), 1)


if __name__ == "__main__":
    unittest.main()
