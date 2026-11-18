from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import sys
from datetime import UTC, datetime
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API_VERSION = "2022-11-28"


def canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_probe_record(
    *,
    event_id: str,
    source_repository: str,
    source_workflow: str,
    source_workflow_ref: str,
    run_id: str,
    run_attempt: int,
    commit_sha: str,
    runner_os: str,
    runner_arch: str,
    python_version: str,
    archive_repository: str,
    observed_at: datetime,
) -> dict:
    if not event_id or not run_id or run_attempt < 1:
        raise ValueError("Probe identity fields are invalid.")
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware.")

    record = {
        "schema_version": 1,
        "record_type": "archive_connectivity_probe",
        "event_id": event_id,
        "source_repository": source_repository,
        "source_workflow": source_workflow,
        "source_workflow_ref": source_workflow_ref,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "source_commit_sha": commit_sha,
        "archive_repository": archive_repository,
        "runner_os": runner_os,
        "runner_arch": runner_arch,
        "python_version": python_version,
        "observed_at_utc": observed_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "timestamp_authority": "runner_clock_diagnostic_only",
        "market_data_collection": "not_performed",
    }
    record["record_hash"] = sha256_text(canonical_json(record))
    return record


def verify_probe_record(record: dict) -> None:
    if record.get("schema_version") != 1:
        raise ValueError("Unsupported probe schema version.")
    if record.get("record_type") != "archive_connectivity_probe":
        raise ValueError("Unexpected record type.")
    if record.get("market_data_collection") != "not_performed":
        raise ValueError("Connectivity probe must never collect market data.")
    if record.get("timestamp_authority") != "runner_clock_diagnostic_only":
        raise ValueError("Runner timestamp cannot be used as provider time.")

    supplied_hash = record.get("record_hash")
    body = {key: value for key, value in record.items() if key != "record_hash"}
    expected_hash = sha256_text(canonical_json(body))
    if supplied_hash != expected_hash:
        raise ValueError("Probe record hash does not verify.")


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
            "User-Agent": "mwi-private-archive-connectivity-probe",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc


def main() -> int:
    token = os.environ.get("MWI_ARCHIVE_TOKEN", "")
    if not token:
        raise RuntimeError("MWI_ARCHIVE_TOKEN secret is missing.")

    source_repository = os.environ["GITHUB_REPOSITORY"]
    archive_repository = os.environ["MWI_ARCHIVE_REPOSITORY"]
    ref = os.environ.get("GITHUB_REF", "")
    if ref != "refs/heads/main":
        raise RuntimeError(f"Refused archive write from non-main ref: {ref}")

    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    event_id = f"{run_id}:{attempt}"
    observed_at = datetime.now(UTC)

    record = build_probe_record(
        event_id=event_id,
        source_repository=source_repository,
        source_workflow=os.environ.get("GITHUB_WORKFLOW", "unknown"),
        source_workflow_ref=os.environ.get("GITHUB_WORKFLOW_REF", "unknown"),
        run_id=run_id,
        run_attempt=attempt,
        commit_sha=os.environ.get("GITHUB_SHA", "unknown"),
        runner_os=os.environ.get("RUNNER_OS", platform.system()),
        runner_arch=os.environ.get("RUNNER_ARCH", platform.machine()),
        python_version=platform.python_version(),
        archive_repository=archive_repository,
        observed_at=observed_at,
    )
    verify_probe_record(record)

    payload_bytes = (canonical_json(record) + "\n").encode("utf-8")
    month = record["observed_at_utc"][:7]
    path = f"data/provider-health/github-actions/{month}/probe-{run_id}-{attempt}.json"
    api_path = quote(path, safe="/")
    query = urlencode({"ref": "main"})
    api_url = f"https://api.github.com/repos/{archive_repository}/contents/{api_path}?{query}"

    existing = None
    try:
        existing = api_call("GET", api_url, token)
    except RuntimeError as exc:
        if "HTTP 404" not in str(exc):
            raise

    if existing is not None:
        if existing.get("encoding") != "base64":
            raise RuntimeError("Existing archive probe has unsupported encoding.")
        existing_bytes = base64.b64decode(existing.get("content", ""))
        if existing_bytes != payload_bytes:
            raise RuntimeError("Archive path already exists with different content.")
        print("PRIVATE_ARCHIVE_PROBE=ALREADY_PRESENT")
        print(f"ARCHIVE_PATH={path}")
        print(f"RECORD_SHA256={record['record_hash']}")
        return 0

    body = {
        "message": f"chore(archive): add connectivity probe {event_id}",
        "content": base64.b64encode(payload_bytes).decode("ascii"),
        "branch": "main",
    }
    result = api_call("PUT", api_url, token, body)
    commit_sha = result.get("commit", {}).get("sha")
    if not commit_sha:
        raise RuntimeError("GitHub did not return the private archive commit SHA.")

    print("PRIVATE_ARCHIVE_PROBE=SUCCESS")
    print(f"ARCHIVE_REPOSITORY={archive_repository}")
    print(f"ARCHIVE_PATH={path}")
    print(f"RECORD_SHA256={record['record_hash']}")
    print(f"ARCHIVE_COMMIT={commit_sha}")
    print("MARKET_DATA_COLLECTION=NOT_PERFORMED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ARCHIVE_PROBE_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
