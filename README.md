# MWI Cloud Runner Infrastructure

A small, public GitHub Actions runner monitor for the Market Wave Intelligence research project.

## Current function

- Verifies that a standard GitHub-hosted Ubuntu runner can be allocated.
- Runs hourly at minute 17 UTC, with manual dispatch available.
- Appends a monthly, public, SHA-256 hash-chained infrastructure heartbeat ledger.
- Runs standard-library unit tests before every heartbeat.
- Uses only the built-in, repository-scoped GITHUB_TOKEN; no personal access token or exchange API key is stored here.

## Important boundaries

This repository currently performs **infrastructure health monitoring only**. It does not collect prices, crowd positioning, derivatives, exchange account data, or any other market data. Runner timestamps are diagnostic metadata only and are not valid exchange/server timestamps for a market-data experiment.

The monthly ledgers under data/runner-health/ are public and contain only workflow/runner metadata. Do not add private research data, credentials, trading logic, private source code, user information, or API keys to this repository.

The private research repository and the frozen prospective Spot protocol remain separate and unchanged.

## Schedule behavior

The workflow is scheduled for minute 17 of every UTC hour to avoid the high-load boundary at the start of the hour. GitHub can still delay or drop scheduled runs during high load. Scheduled workflows run from the default branch; GitHub automatically disables scheduled workflows in public repositories after 60 days without repository activity.

Standard GitHub-hosted runners are free for public repositories. This is not a persistent virtual machine: each workflow run receives a fresh, temporary runner.

## Ledger integrity

Each monthly JSONL ledger has an append-only SHA-256 hash chain. Repeated delivery of the same workflow run/attempt is idempotent. The writer verifies the existing ledger before appending and fails closed if the chain is malformed or tampered with.
