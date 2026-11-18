from __future__ import annotations

import copy
import unittest

from scripts import synthetic_archive_validation as archive


class SyntheticArchiveValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observation_schema = {"type": "object"}
        self.manifest_schema = {"type": "object"}
        self.records = archive.build_synthetic_observations("test-run", 1)

    def test_three_synthetic_records_form_a_valid_hash_chain(self) -> None:
        ledger, terminal_hash = archive.verify_observations(
            self.records, self.observation_schema
        )
        self.assertEqual(len(ledger.splitlines()), 3)
        self.assertEqual(terminal_hash, self.records[-1]["record_hash"])

    def test_payload_tampering_is_rejected(self) -> None:
        records = copy.deepcopy(self.records)
        records[1]["payload"]["synthetic_value"] = 999.0
        with self.assertRaisesRegex(ValueError, "Payload digest mismatch"):
            archive.verify_observations(records, self.observation_schema)

    def test_broken_previous_hash_is_rejected(self) -> None:
        records = copy.deepcopy(self.records)
        records[1]["previous_hash"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "Broken previous_hash"):
            archive.verify_observations(records, self.observation_schema)

    def test_duplicate_event_identifier_is_rejected(self) -> None:
        records = copy.deepcopy(self.records)
        records[1]["event_id"] = records[0]["event_id"]
        with self.assertRaisesRegex(ValueError, "Duplicate synthetic event_id"):
            archive.verify_observations(records, self.observation_schema)

    def test_manifest_matches_serialized_ledger(self) -> None:
        ledger, terminal_hash = archive.verify_observations(
            self.records, self.observation_schema
        )
        manifest = archive.build_and_verify_manifest(
            self.records, ledger, terminal_hash, self.manifest_schema
        )
        self.assertEqual(manifest["record_count"], 3)
        self.assertEqual(manifest["ledger_sha256"], archive.sha256_bytes(ledger))
        self.assertEqual(manifest["audit_status"], "ok")

    def test_schema_validator_rejects_unexpected_properties(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["safe"],
            "properties": {"safe": {"type": "boolean"}},
        }
        with self.assertRaisesRegex(ValueError, "unexpected properties"):
            archive.validate_schema_instance(
                {"safe": True, "secret": "unexpected"}, schema
            )

    def test_schema_validator_enforces_timestamp_and_any_of(self) -> None:
        schema = {
            "type": "object",
            "required": ["created_at"],
            "properties": {
                "created_at": {"type": "string", "format": "date-time"}
            },
            "anyOf": [{"required": ["payload"]}, {"required": ["payload_reference"]}],
        }
        with self.assertRaisesRegex(ValueError, "invalid date-time"):
            archive.validate_schema_instance(
                {"created_at": "not-a-time", "payload": {}}, schema
            )
        with self.assertRaisesRegex(ValueError, "no anyOf alternative matched"):
            archive.validate_schema_instance(
                {"created_at": "2026-01-01T00:00:00Z"}, schema
            )

    def test_bundle_hash_and_synthetic_only_boundary(self) -> None:
        observation_schema = {"type": "object"}
        manifest_schema = {"type": "object"}
        from datetime import UTC, datetime

        bundle = archive.build_validation_bundle(
            run_id="test-run",
            attempt=1,
            commit_sha="a" * 40,
            observation_schema=observation_schema,
            manifest_schema=manifest_schema,
            runner_observed_at=datetime(2026, 11, 18, tzinfo=UTC),
        )
        archive.verify_bundle_hash(bundle)
        self.assertEqual(bundle["market_data_collection"], "not_performed")
        self.assertEqual(bundle["checks"]["hash_chain"], "passed")

        tampered = copy.deepcopy(bundle)
        tampered["market_data_collection"] = "performed"
        with self.assertRaisesRegex(ValueError, "report hash mismatch"):
            archive.verify_bundle_hash(tampered)


if __name__ == "__main__":
    unittest.main()
