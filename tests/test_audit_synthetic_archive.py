from __future__ import annotations

import copy
import unittest
from datetime import UTC, datetime

from scripts import audit_synthetic_archive as auditor
from scripts import synthetic_archive_validation as source


class SyntheticArchiveReadOnlyAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_id = "test-audit-run"
        self.attempt = 1
        self.observation_schema = {"type": "object"}
        self.manifest_schema = {"type": "object"}
        stem = f"run-{self.run_id}-attempt-{self.attempt}"
        base = f"audit/synthetic-validation/fixtures/2026-11/{stem}"
        self.ledger_path = f"{base}.jsonl"
        self.manifest_path = f"{base}.manifest.json"
        self.report_path = f"audit/synthetic-validation/2026-11/{stem}.json"

        self.bundle = source.build_validation_bundle(
            run_id=self.run_id,
            attempt=self.attempt,
            commit_sha="a" * 40,
            observation_schema=self.observation_schema,
            manifest_schema=self.manifest_schema,
            runner_observed_at=datetime(2026, 11, 18, tzinfo=UTC),
            ledger_path=self.ledger_path,
            manifest_path=self.manifest_path,
            report_path=self.report_path,
        )
        self.ledger_bytes = (
            "\n".join(
                source.canonical_json(record)
                for record in self.bundle["observations"]
            )
            + "\n"
        ).encode("utf-8")
        self.manifest_bytes = (
            source.canonical_json(self.bundle["manifest"]) + "\n"
        ).encode("utf-8")
        self.report_bytes = (
            source.canonical_json(self.bundle) + "\n"
        ).encode("utf-8")

    def audit(self, **overrides):
        arguments = {
            "report_path": self.report_path,
            "report_bytes": self.report_bytes,
            "ledger_bytes": self.ledger_bytes,
            "manifest_bytes": self.manifest_bytes,
            "observation_schema": self.observation_schema,
            "manifest_schema": self.manifest_schema,
            "expected_run_id": self.run_id,
            "expected_attempt": self.attempt,
        }
        arguments.update(overrides)
        return auditor.audit_bundle_files(**arguments)

    def test_valid_archive_bundle_passes_independent_read_only_audit(self) -> None:
        result = self.audit()
        self.assertEqual(result["audit_status"], "ok")
        self.assertEqual(result["record_count"], 3)
        self.assertEqual(result["market_data_collection"], "not_performed")
        self.assertEqual(result["ledger_path"], self.ledger_path)
        self.assertEqual(result["manifest_path"], self.manifest_path)

    def test_tampered_report_hash_is_rejected(self) -> None:
        report = copy.deepcopy(self.bundle)
        report["run_id"] = "forged-run"
        report_bytes = (
            source.canonical_json(report) + "\n"
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "report hash mismatch"):
            self.audit(report_bytes=report_bytes)

    def test_tampered_ledger_bytes_are_rejected(self) -> None:
        tampered = self.ledger_bytes.replace(b"101.0", b"999.0", 1)
        with self.assertRaisesRegex(ValueError, "Ledger file digest"):
            self.audit(ledger_bytes=tampered)

    def test_tampered_manifest_bytes_are_rejected(self) -> None:
        tampered = self.manifest_bytes.replace(b'"record_count":3', b'"record_count":4')
        with self.assertRaisesRegex(ValueError, "Manifest file digest"):
            self.audit(manifest_bytes=tampered)

    def test_wrong_run_id_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "run_id does not match"):
            self.audit(expected_run_id="another-run")

    def test_wrong_attempt_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "attempt does not match"):
            self.audit(expected_attempt=2)

    def test_report_path_must_match_archive_metadata(self) -> None:
        with self.assertRaisesRegex(ValueError, "Report path does not match"):
            self.audit(report_path="audit/synthetic-validation/other.json")

    def test_schema_digest_mismatch_is_rejected(self) -> None:
        wrong_schema = {"type": "object", "title": "altered"}
        with self.assertRaisesRegex(ValueError, "Observation schema digest"):
            self.audit(observation_schema=wrong_schema)

    def test_non_synthetic_report_is_rejected_even_when_resigned(self) -> None:
        report = copy.deepcopy(self.bundle)
        report["synthetic_only"] = False
        body = {key: value for key, value in report.items() if key != "report_sha256"}
        report["report_sha256"] = source.sha256_json(body)
        report_bytes = (
            source.canonical_json(report) + "\n"
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "not marked synthetic-only"):
            self.audit(report_bytes=report_bytes)

    def test_market_data_collection_flag_cannot_be_enabled(self) -> None:
        report = copy.deepcopy(self.bundle)
        report["market_data_collection"] = "performed"
        body = {key: value for key, value in report.items() if key != "report_sha256"}
        report["report_sha256"] = source.sha256_json(body)
        report_bytes = (
            source.canonical_json(report) + "\n"
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "must not collect market data"):
            self.audit(report_bytes=report_bytes)

    def test_broken_record_chain_is_rejected(self) -> None:
        records = copy.deepcopy(self.bundle["observations"])
        records[1]["previous_hash"] = "f" * 64
        ledger_bytes = (
            "\n".join(source.canonical_json(record) for record in records) + "\n"
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "Ledger file digest"):
            self.audit(ledger_bytes=ledger_bytes)

    def test_manifest_sidecar_must_match_report_manifest(self) -> None:
        report = copy.deepcopy(self.bundle)
        report["manifest"]["record_count"] = 9
        body = {key: value for key, value in report.items() if key != "report_sha256"}
        report["report_sha256"] = source.sha256_json(body)
        report_bytes = (
            source.canonical_json(report) + "\n"
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "Report manifest differs"):
            self.audit(report_bytes=report_bytes)

    def test_report_required_check_flags_are_enforced(self) -> None:
        report = copy.deepcopy(self.bundle)
        report["checks"]["hash_chain"] = "skipped"
        body = {key: value for key, value in report.items() if key != "report_sha256"}
        report["report_sha256"] = source.sha256_json(body)
        report_bytes = (
            source.canonical_json(report) + "\n"
        ).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "check hash_chain"):
            self.audit(report_bytes=report_bytes)


if __name__ == "__main__":
    unittest.main()
