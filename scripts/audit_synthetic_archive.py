from __future__ import annotations

import json
import os
import sys

from scripts import synthetic_archive_validation as source


def audit_bundle_files(
    *,
    report_path: str,
    report_bytes: bytes,
    ledger_bytes: bytes,
    manifest_bytes: bytes,
    observation_schema: dict,
    manifest_schema: dict,
    expected_run_id: str,
    expected_attempt: int,
) -> dict:
    try:
        report = json.loads(report_bytes.decode("utf-8"))
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Archive report or manifest is not valid UTF-8 JSON") from exc

    source.verify_bundle_hash(report)
    if report.get("record_type") != "synthetic_archive_integration_test":
        raise ValueError("Unexpected archive report type")
    if report.get("synthetic_only") is not True:
        raise ValueError("Archive report is not marked synthetic-only")
    if report.get("market_data_collection") != "not_performed":
        raise ValueError("Archive report does not preserve the no-market-data boundary")
    if report.get("timestamp_authority") != "runner_clock_diagnostic_only":
        raise ValueError("Runner clock is incorrectly represented as authoritative")
    if str(report.get("run_id")) != str(expected_run_id):
        raise ValueError("Archive report run_id does not match this workflow run")
    if report.get("run_attempt") != expected_attempt:
        raise ValueError("Archive report attempt does not match this workflow attempt")

    outputs = report.get("archive_outputs")
    if not isinstance(outputs, dict):
        raise ValueError("Archive report is missing archive_outputs metadata")

    ledger_path = outputs.get("ledger_path")
    manifest_path = outputs.get("manifest_path")
    if outputs.get("report_path") != report_path:
        raise ValueError("Report path does not match its archive metadata")
    source.validate_archive_output_paths([ledger_path, manifest_path, report_path])

    if outputs.get("write_semantics") != (
        "single_git_commit_with_read_after_write_verification"
    ):
        raise ValueError("Unexpected archive write semantics")

    if report.get("observation_schema_sha256") != source.sha256_json(
        observation_schema
    ):
        raise ValueError("Observation schema digest does not match the report")
    if report.get("manifest_schema_sha256") != source.sha256_json(manifest_schema):
        raise ValueError("Manifest schema digest does not match the report")

    if outputs.get("ledger_sha256") != source.sha256_bytes(ledger_bytes):
        raise ValueError("Ledger file digest does not match the report")
    if outputs.get("manifest_sha256") != source.sha256_bytes(manifest_bytes):
        raise ValueError("Manifest file digest does not match the report")

    try:
        ledger_text = ledger_bytes.decode("utf-8")
        records = [
            json.loads(line)
            for line in ledger_text.splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Synthetic ledger is not valid JSONL") from exc

    canonical_ledger, terminal_hash = source.verify_observations(
        records, observation_schema
    )
    if canonical_ledger != ledger_bytes:
        raise ValueError("Ledger is not canonical JSONL or has invalid line endings")
    if report.get("observations") != records:
        raise ValueError("Ledger records differ from the observations in the report")

    expected_manifest = source.build_and_verify_manifest(
        records,
        canonical_ledger,
        terminal_hash,
        manifest_schema,
        ledger_path,
    )
    source.validate_schema_instance(manifest, manifest_schema)
    if manifest != expected_manifest:
        raise ValueError("Stored manifest differs from the independently rebuilt manifest")
    if report.get("manifest") != manifest:
        raise ValueError("Report manifest differs from the stored manifest")

    canonical_manifest_bytes = (
        source.canonical_json(manifest) + "\n"
    ).encode("utf-8")
    if canonical_manifest_bytes != manifest_bytes:
        raise ValueError("Manifest sidecar is not canonical JSON")

    checks = report.get("checks", {})
    required_checks = {
        "observation_schema_validation": "passed",
        "manifest_schema_validation": "passed",
        "payload_sha256": "passed",
        "record_sha256": "passed",
        "hash_chain": "passed",
        "manifest_ledger_sha256": "passed",
        "synthetic_data_boundary": "passed",
        "market_data_collection": "not_performed",
    }
    for key, expected in required_checks.items():
        if checks.get(key) != expected:
            raise ValueError(f"Report check {key} is not {expected}")

    return {
        "report_path": report_path,
        "ledger_path": ledger_path,
        "manifest_path": manifest_path,
        "record_count": len(records),
        "ledger_sha256": source.sha256_bytes(ledger_bytes),
        "manifest_sha256": source.sha256_bytes(manifest_bytes),
        "report_sha256": report["report_sha256"],
        "terminal_record_hash": terminal_hash,
        "audit_status": "ok",
        "market_data_collection": "not_performed",
    }


def main() -> int:
    token = os.environ.get("MWI_ARCHIVE_TOKEN", "")
    if not token:
        raise RuntimeError("MWI_ARCHIVE_TOKEN secret is missing")

    archive_repository = os.environ.get("MWI_ARCHIVE_REPOSITORY", "")
    if archive_repository != source.EXPECTED_ARCHIVE:
        raise RuntimeError("Unexpected private archive repository; refusing audit")

    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Archive audit is allowed only on main")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RuntimeError("Archive audit is manual-dispatch only")

    report_path = os.environ.get("MWI_ARCHIVE_REPORT_PATH", "")
    if not report_path.startswith("audit/synthetic-validation/") or not report_path.endswith(".json"):
        raise RuntimeError("Missing or unsafe synthetic archive report path")

    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))

    report_bytes = source.read_archive_file_bytes(report_path, token)
    report = json.loads(report_bytes.decode("utf-8"))
    ledger_path = report.get("archive_outputs", {}).get("ledger_path", "")
    manifest_path = report.get("archive_outputs", {}).get("manifest_path", "")

    source.validate_archive_output_paths([ledger_path, manifest_path, report_path])

    ledger_bytes = source.read_archive_file_bytes(ledger_path, token)
    manifest_bytes = source.read_archive_file_bytes(manifest_path, token)
    observation_schema = source.fetch_archive_json(
        "schemas/observation-v1.schema.json", token
    )
    manifest_schema = source.fetch_archive_json(
        "schemas/archive-manifest-v1.schema.json", token
    )

    result = audit_bundle_files(
        report_path=report_path,
        report_bytes=report_bytes,
        ledger_bytes=ledger_bytes,
        manifest_bytes=manifest_bytes,
        observation_schema=observation_schema,
        manifest_schema=manifest_schema,
        expected_run_id=run_id,
        expected_attempt=attempt,
    )

    print("SYNTHETIC_ARCHIVE_READ_ONLY_AUDIT=PASS")
    print(f"AUDIT_STATUS={result['audit_status']}")
    print(f"AUDITED_RECORDS={result['record_count']}")
    print(f"AUDITED_LEDGER={result['ledger_path']}")
    print(f"AUDITED_MANIFEST={result['manifest_path']}")
    print(f"AUDITED_REPORT={result['report_path']}")
    print(f"AUDITED_TERMINAL_HASH={result['terminal_record_hash']}")
    print("ARCHIVE_MODIFIED=NO")
    print("MARKET_DATA_COLLECTION=NOT_PERFORMED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"SYNTHETIC_ARCHIVE_READ_ONLY_AUDIT_ERROR={type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)
