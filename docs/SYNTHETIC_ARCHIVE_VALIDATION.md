# Synthetic private-archive integration validation

## Purpose

This is a one-time/manual infrastructure test of the private archive contract. It is deliberately separate from every market-data collection stream.

The workflow downloads the current v1 observation and partition-manifest schemas from the private archive, then creates three clearly marked synthetic fixture records in memory. It verifies:

- the fields, types, constants, patterns, date-time values, and alternatives used by the two current schemas;
- payload SHA-256 values;
- canonical record hashes and the three-record previous-hash chain;
- the manifest record count, first/last hashes, and serialized-ledger digest;
- a final SHA-256 digest over the validation report.

The three synthetic outputs are committed together in one Git commit:

- JSONL fixture ledger under `audit/synthetic-validation/fixtures/<YYYY-MM>/`;
- matching partition manifest beside the ledger;
- validation report under `audit/synthetic-validation/<YYYY-MM>/`.

After the write, the writer reads all three files back and compares their exact bytes. A separate read-only audit step then fetches the files again, independently recomputes the record/payload hashes and chain, checks both live schemas, verifies the manifest and report digests, and confirms the no-market-data boundary.

No fixture is written under `data/spot-wave/` or `data/crowd-signal/`.

## Safety boundaries

- The test uses no exchange endpoint, provider API, account data, or market-data credential.
- Every fixture is explicitly labelled synthetic and uses the dedicated protocol ID `MWI-SYNTHETIC-ARCHIVE-INTEGRATION-TEST-NOT-RESEARCH`.
- The instrument is `SYNTHETIC-DO-NOT-USE`; the fixture must never be used for research, labels, model training, or performance reporting.
- Runner time is included only as diagnostic metadata. It is not provider time or market-data availability evidence.
- The script refuses to write outside the expected private repository, outside `main`, or from a non-manual workflow event.
- A successful test proves that the current schemas and synthetic archive-format checks can be executed and the report can be written. It does not prove provider eligibility, live-data ingestion, or market-data completeness.

## How to run

1. Confirm the `MWI_ARCHIVE_TOKEN` Actions secret and `MWI_ARCHIVE_CONNECTIVITY_ENABLED=true` repository variable exist in the public runner repository.
2. Open **Actions → MWI Cloud Runner Health Ledger → Run workflow**.
3. Select `main` and start the manual run.
4. Confirm **Validate synthetic archive format and write integration report** succeeds.
5. Confirm **Independently audit private archive readback** succeeds. This step is read-only and does not modify the archive.

No PowerShell command or local script execution is required.
