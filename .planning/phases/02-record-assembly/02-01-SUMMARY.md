---
phase: 02-record-assembly
plan: "01"
subsystem: fetch
tags: [fetch, hubspot, pagination, dlq, retry]
dependency_graph:
  requires: []
  provides: [scripts/fetch_list.py, "$RUNNER_TEMP/contact_ids.json (runtime)"]
  affects: [scripts/fetch_record.py, .github/workflows/campaign.yml]
tech_stack:
  added: []
  patterns: [cursor-pagination, DLQ-sentinel, REQ_RETRY_KWARGS]
key_files:
  created: [scripts/fetch_list.py]
  modified: []
decisions:
  - "Cursor-based pagination follows HubSpot Lists API v3 native pattern (after cursor)"
  - "Full contact ID list built in memory before single json.dump — no partial file on crash (T-02-03)"
  - "DLQ sentinel written at startup before any API call — crash trace guaranteed (ERR-02)"
metrics:
  duration: "3 minutes"
  completed_date: "2026-08-21"
---

# Phase 2 Plan 1: fetch_list.py — Paginated HubSpot List Fetch Summary

**One-liner:** Cursor-paginated HubSpot Lists API v3 fetch with DLQ sentinel + REQ_RETRY_KWARGS writing all contact IDs to RUNNER_TEMP/contact_ids.json.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Implement fetch_list.py — paginated list fetch + contact_ids.json write | 1c85ba3 | scripts/fetch_list.py (created, 101 lines) |

## What Was Built

`scripts/fetch_list.py` is the first script in the pipeline. It:

1. Reads `INPUT_LIST_ID` and `HUBSPOT_API_KEY` from environment (loud KeyError failure if absent).
2. Writes a DLQ sentinel record at startup before any API call so a pre-try/except crash still leaves a trace in `failed_contacts.json`.
3. Paginates through all pages of `GET /crm/v3/lists/{listId}/memberships` using an `after` cursor, accumulating `recordId` values as strings.
4. Builds the full list in memory, then performs a single `json.dump` to `$RUNNER_TEMP/contact_ids.json` — no partial file possible on crash.
5. On any exception: calls `write_dlq(INPUT_LIST_ID, "", "fetch_list", str(e), 0)` and exits non-zero.
6. On success: prints `Fetched {N} contact IDs from list {INPUT_LIST_ID}`.

## Verification

All three plan verification steps passed:

- `python -c "import ast; ast.parse(...)"` → `Syntax OK`
- All 7 structure assertions (INPUT_LIST_ID, contact_ids.json, write_dlq, REQ_RETRY_KWARGS, raise_for_status, paging, sys.exit(1)) → `All structure checks passed`
- `import utils` from scripts/ → `utils import OK`

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — fetch_list.py is complete and functional with no placeholder data paths.

## Threat Flags

None — no new security surface beyond what the plan's threat model covers. HUBSPOT_API_KEY is never logged; contact_ids.json is a runtime artefact written to RUNNER_TEMP (T-02-01 accepted, T-02-03 mitigated by single-dump approach).

## Self-Check: PASSED

- `scripts/fetch_list.py` exists: FOUND
- Commit `1c85ba3` exists: FOUND
