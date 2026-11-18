from __future__ import annotations

import json
import unittest

from scripts.runner_heartbeat import append_payload, verify_ledger


def sample_payload(event_id: str = "12345:1") -> dict:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "observed_at_utc": "2026-11-18T12:17:00Z",
        "trigger": "workflow_dispatch",
        "market_data_collection": "not_performed",
    }


class RunnerHeartbeatLedgerTests(unittest.TestCase):
    def test_append_creates_verifiable_hash_chain(self) -> None:
        first, appended = append_payload(b"", sample_payload("100:1"))
        second, appended_second = append_payload(first, sample_payload("101:1"))

        self.assertTrue(appended)
        self.assertTrue(appended_second)
        records = verify_ledger(second)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["previous_hash"], records[0]["record_hash"])

    def test_duplicate_event_is_idempotent(self) -> None:
        ledger, appended = append_payload(b"", sample_payload())
        unchanged, appended_again = append_payload(ledger, sample_payload())

        self.assertTrue(appended)
        self.assertFalse(appended_again)
        self.assertEqual(unchanged, ledger)

    def test_tampered_record_is_rejected(self) -> None:
        ledger, _ = append_payload(b"", sample_payload())
        row = json.loads(ledger.decode("utf-8"))
        row["trigger"] = "tampered"
        corrupted = (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()

        with self.assertRaisesRegex(ValueError, "Invalid record_hash"):
            verify_ledger(corrupted)

    def test_broken_chain_is_rejected(self) -> None:
        first, _ = append_payload(b"", sample_payload("100:1"))
        second, _ = append_payload(first, sample_payload("101:1"))
        rows = [json.loads(line) for line in second.decode("utf-8").splitlines()]
        rows[1]["previous_hash"] = "f" * 64
        corrupted = b"".join(
            (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
            for row in rows
        )

        with self.assertRaisesRegex(ValueError, "Broken previous_hash"):
            verify_ledger(corrupted)

    def test_duplicate_ids_inside_existing_ledger_are_rejected(self) -> None:
        ledger, _ = append_payload(b"", sample_payload())
        corrupted = ledger + ledger

        with self.assertRaisesRegex(ValueError, "Duplicate event_id"):
            verify_ledger(corrupted)


if __name__ == "__main__":
    unittest.main()
