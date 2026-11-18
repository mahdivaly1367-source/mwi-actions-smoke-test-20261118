from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ZERO_HASH = "0" * 64
API_VERSION = "2022-11-28"


def canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def record_hash(value: dict) -> str:
    body = {key: item for key, item in value.items() if key != "record_hash"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def verify_ledger(raw: bytes) -> list[dict]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        raise ValueError("Ledger must end with a newline.")

    previous_hash = ZERO_HASH
    event_ids: set[str] = set()
    records: list[dict] = []

    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line:
            raise ValueError(f"Blank ledger line at {line_number}.")
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"Ledger record {line_number} is not an object.")

        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError(f"Missing event_id at line {line_number}.")
        if event_id in event_ids:
            raise ValueError(f"Duplicate event_id at line {line_number}.")
        if record.get("previous_hash") != previous_hash:
            raise ValueError(f"Broken previous_hash at line {line_number}.")

        expected_hash = record_hash(record)
        if record.get("record_hash") != expected_hash:
            raise ValueError(f"Invalid record_hash at line {line_number}.")

        event_ids.add(event_id)
        records.append(record)
        previous_hash = expected_hash

    return records


def append_payload(raw: bytes, payload: dict) -> tuple[bytes, bool]:
    records = verify_ledger(raw)
    event_id = payload.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("Payload requires a non-empty event_id.")

    if any(row["event_id"] == event_id for row in records):
        return raw, False

    previous_hash = records[-1]["record_hash"] if records else ZERO_HASH
    record = dict(payload)
    record["previous_hash"] = previous_hash
    record["record_hash"] = record_hash(record)
    return raw + (canonical_json(record) + "\n").encode("utf-8"), True


def build_payload(now: datetime | None = None) -> dict:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise ValueError("Heartbeat timestamp must be timezone-aware.")

    run_id = os.environ["GITHUB_RUN_ID"]
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    return {
        "schema_version": 1,
        "event_id": f"{run_id}:{attempt}",
        "observed_at_utc": moment.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "workflow": os.environ.get("GITHUB_WORKFLOW", "unknown"),
        "workflow_ref": os.environ.get("GITHUB_WORKFLOW_REF", "unknown"),
        "trigger": os.environ.get("GITHUB_EVENT_NAME", "unknown"),
        "run_id": run_id,
        "run_attempt": int(attempt),
        "commit_sha": os.environ.get("GITHUB_SHA", "unknown"),
        "runner_os": os.environ.get("RUNNER_OS", platform.system()),
        "runner_arch": os.environ.get("RUNNER_ARCH", platform.machine()),
        "python_version": platform.python_version(),
        "market_data_collection": "not_performed",
        "timestamp_authority": "runner_clock_diagnostic_only",
    }


def api_request(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "mwi-cloud-runner-health-ledger",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc


def load_remote_ledger(api_url: str, token: str) -> tuple[bytes, str | None]:
    request = Request(
        api_url,
        method="GET",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "mwi-cloud-runner-health-ledger",
        },
    )
    try:
        with urlopen(request, timeout=25) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return b"", None
        detail = exc.read(2048).decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}: {detail}") from exc

    if result.get("encoding") != "base64" or not result.get("content"):
        raise RuntimeError("Remote ledger response is not a readable Base64 file.")
    return base64.b64decode(result["content"]), result["sha"]


def main() -> int:
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    ref = os.environ.get("GITHUB_REF", "")
    if ref != "refs/heads/main":
        raise RuntimeError(f"Refused to write from non-default branch: {ref}")

    payload = build_payload()
    month_path = f"data/runner-health/{payload['observed_at_utc'][:7]}.jsonl"
    encoded_path = quote(month_path, safe="/")
    query = urlencode({"ref": "main"})
    api_url = f"https://api.github.com/repos/{repository}/contents/{encoded_path}?{query}"

    raw, existing_sha = load_remote_ledger(api_url, token)
    updated, appended = append_payload(raw, payload)
    records = verify_ledger(updated)

    if not appended:
        print("HEARTBEAT_DUPLICATE_SKIPPED=1")
        print(f"LEDGER_PATH={month_path}")
        print(f"LEDGER_RECORDS={len(records)}")
        return 0

    body = {
        "message": f"chore(health): append runner heartbeat {payload['event_id']}",
        "content": base64.b64encode(updated).decode("ascii"),
        "branch": "main",
    }
    if existing_sha:
        body["sha"] = existing_sha

    result = api_request("PUT", api_url, token, body)
    commit_sha = result.get("commit", {}).get("sha")
    if not commit_sha:
        raise RuntimeError("GitHub did not return the ledger commit SHA.")

    tail_hash = records[-1]["record_hash"]
    print("HEARTBEAT_APPEND=SUCCESS")
    print(f"LEDGER_PATH={month_path}")
    print(f"LEDGER_RECORDS={len(records)}")
    print(f"LEDGER_TAIL_SHA256={tail_hash}")
    print(f"LEDGER_COMMIT={commit_sha}")
    print(f"RUNNER_OBSERVED_AT_UTC={payload['observed_at_utc']}")
    print("MARKET_DATA_COLLECTION=NOT_PERFORMED")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        summary = [
            "## MWI cloud runner heartbeat",
            "",
            "- Result: successful",
            f"- Runner-observed UTC: {payload['observed_at_utc']}",
            f"- Trigger: {payload['trigger']}",
            f"- Ledger: {month_path}",
            f"- Records in monthly ledger: {len(records)}",
            f"- Ledger tail SHA-256: {tail_hash}",
            "- Market data collection: not performed",
        ]
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write("\n".join(summary) + "\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"HEARTBEAT_ERROR={type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
