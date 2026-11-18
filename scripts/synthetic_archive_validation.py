from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import sys
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_VERSION = "2022-11-28"
EXPECTED_ARCHIVE = "mahdivaly1367-source/mwi-research-data-archive"
GENESIS_HASH = "0" * 64
SYNTHETIC_PROTOCOL = "MWI-SYNTHETIC-ARCHIVE-INTEGRATION-TEST-NOT-RESEARCH"
SYNTHETIC_TIMESTAMP = "2026-01-01T00:00:00Z"


class GitHubAPIError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(f"GitHub API returned HTTP {status}: {detail}")
        self.status = status


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("date-time values must include a timezone")
    return parsed


def _matches_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise ValueError(f"Unsupported JSON Schema type in archive contract: {expected}")


def validate_schema_instance(value: object, schema: dict, path: str = "$") -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        choices = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_type(value, choice) for choice in choices):
            raise ValueError(f"{path}: expected type {expected_type}")

    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path}: value does not match const")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is not an allowed enum member")

    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                raise ValueError(f"{path}: missing required property {name}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unexpected = set(value) - set(properties)
            if unexpected:
                raise ValueError(f"{path}: unexpected properties {sorted(unexpected)}")
        for name, child in properties.items():
            if name in value:
                validate_schema_instance(value[name], child, f"{path}.{name}")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path}: string shorter than minLength")
        pattern = schema.get("pattern")
        if pattern and re.fullmatch(pattern, value) is None:
            raise ValueError(f"{path}: string does not match required pattern")
        if schema.get("format") == "date-time":
            try:
                parse_datetime(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}: invalid date-time") from exc

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path}: value below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path}: value above maximum")

    alternatives = schema.get("anyOf")
    if alternatives:
        errors = []
        for option in alternatives:
            try:
                validate_schema_instance(value, option, path)
                break
            except ValueError as exc:
                errors.append(str(exc))
        else:
            raise ValueError(f"{path}: no anyOf alternative matched")


def api_call(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "mwi-synthetic-archive-validation",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise GitHubAPIError(exc.code, detail) from exc


def fetch_archive_json(path: str, token: str) -> dict:
    query = urlencode({"ref": "main"})
    api_path = quote(path, safe="/")
    url = (
        f"https://api.github.com/repos/{EXPECTED_ARCHIVE}/contents/"
        f"{api_path}?{query}"
    )
    response = api_call("GET", url, token)
    if response.get("encoding") != "base64":
        raise RuntimeError(f"Archive schema {path} has unexpected encoding")
    raw = base64.b64decode(response.get("content", ""), validate=False)
    return json.loads(raw.decode("utf-8"))


def build_synthetic_observations(run_id: str, attempt: int) -> list[dict]:
    records = []
    previous_hash = GENESIS_HASH

    for sequence in range(1, 4):
        payload = {
            "fixture_only": True,
            "sequence": sequence,
            "synthetic_value": 100.0 + sequence,
            "warning": "NOT REAL MARKET DATA; NEVER USE FOR RESEARCH OR MODEL TRAINING",
        }
        payload_hash = sha256_json(payload)
        timestamp = f"2026-01-01T0{sequence - 1}:00:00Z"
        record = {
            "schema_version": 1,
            "record_type": "market_observation",
            "protocol_id": SYNTHETIC_PROTOCOL,
            "event_id": f"synthetic-{run_id}-{attempt}-{sequence:03d}",
            "provider_id": "synthetic_fixture_only",
            "venue": "SYNTHETIC",
            "market_type": "synthetic_test_only",
            "endpoint_id": "synthetic_fixture_v1",
            "instrument": "SYNTHETIC-DO-NOT-USE",
            "interval": "1h",
            "event_timestamp_utc": timestamp,
            "event_timestamp_semantics": "fixed synthetic fixture timestamp; not provider time",
            "provider_published_at_utc": None,
            "request_started_at_utc": timestamp,
            "local_received_at_utc": timestamp,
            "provider_server_time_before_utc": None,
            "provider_server_time_after_utc": None,
            "available_at_utc": None,
            "timestamp_authority": {
                "event_time": "synthetic_fixture_only_not_provider_time",
                "availability_time": "not_available_synthetic_fixture",
                "local_receipt_time": "local_host_diagnostic_only",
            },
            "http_status": None,
            "content_type": None,
            "response_sha256": None,
            "payload_sha256": payload_hash,
            "payload": payload,
            "previous_hash": previous_hash,
        }
        record["record_hash"] = sha256_json(record)
        previous_hash = record["record_hash"]
        records.append(record)

    return records


def verify_observations(records: list[dict], schema: dict) -> tuple[bytes, str]:
    if len(records) != 3:
        raise ValueError("Synthetic integration fixture must contain exactly three records")

    seen_ids = set()
    expected_previous = GENESIS_HASH
    lines = []

    for index, record in enumerate(records, start=1):
        validate_schema_instance(record, schema)
        event_id = record["event_id"]
        if event_id in seen_ids:
            raise ValueError("Duplicate synthetic event_id")
        seen_ids.add(event_id)

        if record["protocol_id"] != SYNTHETIC_PROTOCOL:
            raise ValueError("Synthetic fixture protocol boundary failed")
        if record["provider_id"] != "synthetic_fixture_only":
            raise ValueError("Synthetic provider boundary failed")
        if record["market_type"] != "synthetic_test_only":
            raise ValueError("Synthetic market-type boundary failed")
        if record["instrument"] != "SYNTHETIC-DO-NOT-USE":
            raise ValueError("Synthetic instrument boundary failed")
        if record["payload"].get("fixture_only") is not True:
            raise ValueError("Synthetic fixture marker is missing")
        if record["previous_hash"] != expected_previous:
            raise ValueError(f"Broken previous_hash at record {index}")

        payload_hash = sha256_json(record["payload"])
        if record["payload_sha256"] != payload_hash:
            raise ValueError(f"Payload digest mismatch at record {index}")

        body = {key: value for key, value in record.items() if key != "record_hash"}
        expected_hash = sha256_json(body)
        if record["record_hash"] != expected_hash:
            raise ValueError(f"Record hash mismatch at record {index}")

        expected_previous = record["record_hash"]
        lines.append(canonical_json(record))

    ledger_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    return ledger_bytes, expected_previous


def build_and_verify_manifest(
    records: list[dict],
    ledger_bytes: bytes,
    last_hash: str,
    schema: dict,
    partition_path: str,
) -> dict:
    manifest = {
        "schema_version": 1,
        "protocol_id": SYNTHETIC_PROTOCOL,
        "provider_id": "synthetic_fixture_only",
        "market_type": "synthetic_test_only",
        "endpoint_id": "synthetic_fixture_v1",
        "partition_path": partition_path,
        "record_count": len(records),
        "first_event_timestamp_utc": records[0]["event_timestamp_utc"],
        "last_event_timestamp_utc": records[-1]["event_timestamp_utc"],
        "first_record_hash": records[0]["record_hash"],
        "last_record_hash": last_hash,
        "previous_partition_terminal_hash": GENESIS_HASH,
        "ledger_sha256": sha256_bytes(ledger_bytes),
        "audit_status": "ok",
    }
    validate_schema_instance(manifest, schema)

    if manifest["record_count"] != len(records):
        raise ValueError("Manifest record_count mismatch")
    if manifest["first_record_hash"] != records[0]["record_hash"]:
        raise ValueError("Manifest first_record_hash mismatch")
    if manifest["last_record_hash"] != records[-1]["record_hash"]:
        raise ValueError("Manifest last_record_hash mismatch")
    if manifest["ledger_sha256"] != sha256_bytes(ledger_bytes):
        raise ValueError("Manifest ledger_sha256 mismatch")

    return manifest


def build_validation_bundle(
    *,
    run_id: str,
    attempt: int,
    commit_sha: str,
    observation_schema: dict,
    manifest_schema: dict,
    runner_observed_at: datetime,
    ledger_path: str,
    manifest_path: str,
    report_path: str,
) -> dict:
    if runner_observed_at.tzinfo is None:
        raise ValueError("runner_observed_at must be timezone-aware")

    records = build_synthetic_observations(run_id, attempt)
    ledger_bytes, last_hash = verify_observations(records, observation_schema)
    manifest = build_and_verify_manifest(
        records, ledger_bytes, last_hash, manifest_schema, ledger_path
    )

    bundle = {
        "schema_version": 1,
        "record_type": "synthetic_archive_integration_test",
        "synthetic_only": True,
        "market_data_collection": "not_performed",
        "timestamp_authority": "runner_clock_diagnostic_only",
        "run_id": run_id,
        "run_attempt": attempt,
        "source_repository": "mahdivaly1367-source/mwi-actions-smoke-test-20261118",
        "source_commit_sha": commit_sha,
        "runner_os": platform.system(),
        "runner_arch": platform.machine(),
        "python_version": platform.python_version(),
        "runner_observed_at_utc": (
            runner_observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        ),
        "observation_schema_sha256": sha256_json(observation_schema),
        "manifest_schema_sha256": sha256_json(manifest_schema),
        "observations": records,
        "manifest": manifest,
        "archive_outputs": {
            "ledger_path": ledger_path,
            "ledger_sha256": sha256_bytes(ledger_bytes),
            "manifest_path": manifest_path,
            "manifest_sha256": sha256_bytes(
                (canonical_json(manifest) + "\n").encode("utf-8")
            ),
            "report_path": report_path,
            "write_semantics": "single_git_commit_with_read_after_write_verification",
        },
        "checks": {
            "observation_schema_validation": "passed",
            "manifest_schema_validation": "passed",
            "payload_sha256": "passed",
            "record_sha256": "passed",
            "hash_chain": "passed",
            "manifest_ledger_sha256": "passed",
            "synthetic_data_boundary": "passed",
            "market_data_collection": "not_performed",
        },
    }
    bundle["report_sha256"] = sha256_json(bundle)
    return bundle


def verify_bundle_hash(bundle: dict) -> None:
    supplied = bundle.get("report_sha256")
    body = {key: value for key, value in bundle.items() if key != "report_sha256"}
    if supplied != sha256_json(body):
        raise ValueError("Synthetic validation report hash mismatch")
    if bundle.get("synthetic_only") is not True:
        raise ValueError("Report is not marked synthetic-only")
    if bundle.get("market_data_collection") != "not_performed":
        raise ValueError("Synthetic test must not collect market data")


def validate_archive_output_paths(paths: list[str]) -> None:
    if len(paths) != 3 or len(set(paths)) != 3:
        raise ValueError("Exactly three unique archive output paths are required")
    for path in paths:
        if not path.startswith("audit/synthetic-validation/"):
            raise ValueError("Synthetic fixture writes are restricted to audit/synthetic-validation/")
        if ".." in path.split("/") or path.startswith("/"):
            raise ValueError("Unsafe archive path")
        if not (
            path.endswith(".json")
            or path.endswith(".jsonl")
        ):
            raise ValueError("Unexpected archive output file extension")
    if not any(path.endswith(".jsonl") for path in paths):
        raise ValueError("Synthetic JSONL partition is missing")
    if not any(path.endswith(".manifest.json") for path in paths):
        raise ValueError("Synthetic partition manifest is missing")
    if not any(path.endswith(".json") and not path.endswith(".manifest.json") for path in paths):
        raise ValueError("Synthetic validation report is missing")


def build_git_tree_entries(blob_shas: dict[str, str]) -> list[dict]:
    return [
        {
            "path": path,
            "mode": "100644",
            "type": "blob",
            "sha": blob_shas[path],
        }
        for path in sorted(blob_shas)
    ]


def archive_contents_url(path: str) -> str:
    api_path = quote(path, safe="/")
    query = urlencode({"ref": "main"})
    return (
        f"https://api.github.com/repos/{EXPECTED_ARCHIVE}/contents/"
        f"{api_path}?{query}"
    )


def read_archive_file_bytes(path: str, token: str) -> bytes:
    response = api_call("GET", archive_contents_url(path), token)
    if response.get("encoding") != "base64":
        raise RuntimeError(f"Archive file {path} has unexpected encoding")
    return base64.b64decode(response.get("content", ""), validate=False)


def verify_archive_readback(expected_files: dict[str, bytes], token: str) -> None:
    for path, expected_bytes in expected_files.items():
        actual_bytes = read_archive_file_bytes(path, token)
        if actual_bytes != expected_bytes:
            raise RuntimeError(f"Archive read-after-write mismatch: {path}")


def write_files_atomically(
    files: dict[str, bytes],
    token: str,
    *,
    run_id: str,
    attempt: int,
) -> tuple[str, str]:
    paths = list(files)
    validate_archive_output_paths(paths)

    existing: dict[str, bytes] = {}
    for path in paths:
        try:
            existing[path] = read_archive_file_bytes(path, token)
        except GitHubAPIError as exc:
            if exc.status != 404:
                raise

    if existing:
        if len(existing) == len(files) and all(
            existing[path] == files[path] for path in files
        ):
            verify_archive_readback(files, token)
            return "ALREADY_PRESENT", "READBACK_VERIFIED"
        raise RuntimeError(
            "Partial or conflicting synthetic archive transaction already exists; refusing overwrite"
        )

    base_ref = api_call(
        "GET",
        f"https://api.github.com/repos/{EXPECTED_ARCHIVE}/git/ref/heads/main",
        token,
    )
    base_commit_sha = base_ref.get("object", {}).get("sha")
    if not base_commit_sha:
        raise RuntimeError("Could not resolve private archive main reference")

    base_commit = api_call(
        "GET",
        f"https://api.github.com/repos/{EXPECTED_ARCHIVE}/git/commits/{base_commit_sha}",
        token,
    )
    base_tree_sha = base_commit.get("tree", {}).get("sha")
    if not base_tree_sha:
        raise RuntimeError("Could not resolve private archive base tree")

    blob_shas: dict[str, str] = {}
    for path, content in files.items():
        blob = api_call(
            "POST",
            f"https://api.github.com/repos/{EXPECTED_ARCHIVE}/git/blobs",
            token,
            {
                "content": content.decode("utf-8"),
                "encoding": "utf-8",
            },
        )
        blob_sha = blob.get("sha")
        if not blob_sha:
            raise RuntimeError(f"GitHub did not return a blob SHA for {path}")
        blob_shas[path] = blob_sha

    tree = api_call(
        "POST",
        f"https://api.github.com/repos/{EXPECTED_ARCHIVE}/git/trees",
        token,
        {
            "base_tree": base_tree_sha,
            "tree": build_git_tree_entries(blob_shas),
        },
    )
    tree_sha = tree.get("sha")
    if not tree_sha:
        raise RuntimeError("GitHub did not return a transaction tree SHA")

    commit = api_call(
        "POST",
        f"https://api.github.com/repos/{EXPECTED_ARCHIVE}/git/commits",
        token,
        {
            "message": (
                "test(archive): atomic synthetic ledger, manifest and report "
                f"{run_id}:{attempt}"
            ),
            "tree": tree_sha,
            "parents": [base_commit_sha],
        },
    )
    commit_sha = commit.get("sha")
    if not commit_sha:
        raise RuntimeError("GitHub did not return the atomic archive commit SHA")

    api_call(
        "PATCH",
        f"https://api.github.com/repos/{EXPECTED_ARCHIVE}/git/refs/heads/main",
        token,
        {
            "sha": commit_sha,
            "force": False,
        },
    )

    verify_archive_readback(files, token)
    return commit_sha, "READBACK_VERIFIED"


def main() -> int:
    token = os.environ.get("MWI_ARCHIVE_TOKEN", "")
    if not token:
        raise RuntimeError("MWI_ARCHIVE_TOKEN secret is missing")

    archive_repository = os.environ.get("MWI_ARCHIVE_REPOSITORY", "")
    if archive_repository != EXPECTED_ARCHIVE:
        raise RuntimeError("Unexpected private archive repository; refusing write")

    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Synthetic archive validation is allowed only on main")
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RuntimeError("Synthetic archive validation is manual-dispatch only")

    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    commit_sha = os.environ.get("GITHUB_SHA", "unknown")
    observed_at = datetime.now(UTC)

    observation_schema = fetch_archive_json(
        "schemas/observation-v1.schema.json", token
    )
    manifest_schema = fetch_archive_json(
        "schemas/archive-manifest-v1.schema.json", token
    )
    month = observed_at.strftime("%Y-%m")
    stem = f"run-{run_id}-attempt-{attempt}"
    base_path = f"audit/synthetic-validation/fixtures/{month}/{stem}"
    ledger_path = f"{base_path}.jsonl"
    manifest_path = f"{base_path}.manifest.json"
    report_path = f"audit/synthetic-validation/{month}/{stem}.json"

    bundle = build_validation_bundle(
        run_id=run_id,
        attempt=attempt,
        commit_sha=commit_sha,
        observation_schema=observation_schema,
        manifest_schema=manifest_schema,
        runner_observed_at=observed_at,
        ledger_path=ledger_path,
        manifest_path=manifest_path,
        report_path=report_path,
    )
    verify_bundle_hash(bundle)

    ledger_bytes = (
        "\n".join(canonical_json(record) for record in bundle["observations"]) + "\n"
    ).encode("utf-8")
    manifest_bytes = (canonical_json(bundle["manifest"]) + "\n").encode("utf-8")
    report_bytes = (canonical_json(bundle) + "\n").encode("utf-8")

    files = {
        ledger_path: ledger_bytes,
        manifest_path: manifest_bytes,
        report_path: report_bytes,
    }
    validate_archive_output_paths(list(files))
    commit_result, readback_result = write_files_atomically(
        files, token, run_id=run_id, attempt=attempt
    )

    print("SYNTHETIC_ARCHIVE_VALIDATION=SUCCESS")
    print(f"ARCHIVE_REPOSITORY={archive_repository}")
    print(f"LEDGER_PATH={ledger_path}")
    print(f"MANIFEST_PATH={manifest_path}")
    print(f"REPORT_PATH={report_path}")
    print(f"REPORT_SHA256={bundle['report_sha256']}")
    print(f"ARCHIVE_ATOMIC_COMMIT={commit_result}")
    print(f"ARCHIVE_READBACK={readback_result}")
    print("SYNTHETIC_RECORDS=3")
    print("ARCHIVE_FILES_COMMITTED_TOGETHER=3")
    print("MARKET_DATA_COLLECTION=NOT_PERFORMED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"SYNTHETIC_ARCHIVE_VALIDATION_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
