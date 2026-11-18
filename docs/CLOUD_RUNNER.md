# Cloud runner architecture and operating boundary

## Purpose

This repository is a public infrastructure monitor, not a market-data collector. It tests the availability of GitHub-hosted Ubuntu runners and records durable, public heartbeat metadata.

## Workflow

- Triggers: hourly schedule at minute 17 UTC, manual dispatch, and pushes to main.
- Runner: standard ubuntu-latest hosted runner.
- Concurrency: one heartbeat writer at a time.
- Dependency: GitHub-maintained actions/checkout, pinned to the full commit SHA for v7.0.1.
- Permissions: only contents: write, used by the built-in workflow token.
- Tests: Python standard-library unit tests; no third-party Python packages.
- Persistence: one JSONL file per UTC month under data/runner-health/.

## Public heartbeat schema

Each record contains:
- unique workflow run ID and attempt;
- runner-observed UTC timestamp;
- workflow name/reference and triggering event;
- source commit SHA;
- runner OS, architecture, and Python version;
- explicit market_data_collection=not_performed;
- preceding record hash and current record hash.

The runner clock is not an exchange clock. No feature or label from the Spot or Crowd research protocols may use this timestamp as provider time or data availability time.

## Ledger rules

1. A monthly ledger is verified from genesis before every append.
2. Each record hash is SHA-256 over canonical JSON excluding the record_hash field.
3. previous_hash must equal the preceding record hash.
4. Duplicate event IDs are rejected by the verifier and skipped by the append operation.
5. Any malformed, incomplete, duplicate, or tampered ledger causes a hard failure; no repair or rewrite is attempted.

## Data protection boundary

The repository is public. It must not contain private research datasets, exchange API keys, personal access tokens, strategy parameters, private source code, or personal information. The workflow currently does not call any exchange or market-data endpoint.

## Future private data sink

A future connector to private research storage must use a dedicated fine-grained credential limited to the one private destination repository, with contents write access only. A broad classic personal access token must never be copied into this public repository's Actions secrets. Until that credential and the destination storage design are explicitly prepared, no private dataset is uploaded from this runner.
