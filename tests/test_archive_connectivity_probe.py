from __future__ import annotations

import unittest
from datetime import UTC, datetime

from scripts.archive_connectivity_probe import (
    build_probe_record,
    canonical_json,
    verify_probe_record,
)


def make_record() -> dict:
    return build_probe_record(
        event_id="123:1",
        source_repository="owner/public-runner",
        source_workflow="health",
        source_workflow_ref="owner/public-runner/.github/workflows/health.yml@refs/heads/main",
        run_id="123",
        run_attempt=1,
        commit_sha="a" * 40,
        runner_os="Linux",
        runner_arch="X64",
        python_version="3.14.4",
        archive_repository="owner/private-archive",
        observed_at=datetime(2026, 11, 18, 12, 0, tzinfo=UTC),
    )


class ArchiveConnectivityProbeTests(unittest.TestCase):
    def test_valid_probe_verifies(self) -> None:
        record = make_record()
        verify_probe_record(record)
        self.assertEqual(record["market_data_collection"], "not_performed")

    def test_tampering_is_rejected(self) -> None:
        record = make_record()
        record["source_repository"] = "attacker/other"
        with self.assertRaisesRegex(ValueError, "hash does not verify"):
            verify_probe_record(record)

    def test_runner_clock_is_never_provider_time(self) -> None:
        record = make_record()
        record["timestamp_authority"] = "provider_server"
        with self.assertRaisesRegex(ValueError, "cannot be used as provider time"):
            verify_probe_record(record)

    def test_probe_cannot_claim_market_data_collection(self) -> None:
        record = make_record()
        record["market_data_collection"] = "performed"
        with self.assertRaisesRegex(ValueError, "must never collect market data"):
            verify_probe_record(record)

    def test_hash_excludes_only_hash_field(self) -> None:
        record = make_record()
        body = {key: value for key, value in record.items() if key != "record_hash"}
        self.assertEqual(record["record_hash"], __import__("hashlib").sha256(
            canonical_json(body).encode("utf-8")
        ).hexdigest())


if __name__ == "__main__":
    unittest.main()
