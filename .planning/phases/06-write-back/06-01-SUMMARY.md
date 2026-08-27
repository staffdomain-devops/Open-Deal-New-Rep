---
phase: 06-write-back
plan: 01
subsystem: write-back
tags: [hubspot, batch-write, schema-check, bracket-guard, dlq, tenacity]
dependency_graph:
  requires:
    - 05-02-SUMMARY.md  # assembled_{cid}.json + lint_passing_ids.json produced by assemble_bodies.py
  provides:
    - scripts/write_hubspot.py  # property schema check, bracket guard, batch property write, DLQ
  affects:
    - HubSpot contact records (12 properties written per contact)
tech_stack:
  added: []
  patterns:
    - "@retry(**HS_RETRY_KWARGS) on batch write function (consistent with utils.py pattern)"
    - "PROPERTY_MAP as ordered list of (prop_name, (top_key, field)) tuples"
    - "EMAIL_PROP_NAMES frozenset for O(1) bracket-guard membership test"
    - "DLQ sentinel at startup before contact loop (ERR-02 pattern)"
key_files:
  created:
    - scripts/write_hubspot.py
  modified: []
decisions:
  - "_check_property_schema not decorated with @retry: wrong field_type is a config error, not transient; fail fast"
  - "EMAIL_PROP_NAMES uses startswith('email_') filter against PROPERTY_MAP — single source of truth, no separate list to maintain"
  - "assembled file absence skips contact without DLQ write: file absence is a pipeline gap from assemble_bodies.py, not a write failure"
metrics:
  duration_seconds: 80
  completed_date: "2026-08-27"
  tasks_completed: 1
  tasks_total: 1
  files_created: 1
  files_modified: 0
---

# Phase 6 Plan 01: Write-back Property Schema Check, Bracket Guard, and Batch Write Summary

**One-liner:** HubSpot property schema validation, bracket-placeholder guard, and 100-contact batch write for all 12 contact properties with DLQ integration.

## What Was Built

`scripts/write_hubspot.py` is the first half of the write-back stage. It:

1. Validates that all 12 HubSpot contact properties (`email_1_subject` through `email_5_body`, `task_note_1`, `task_note_2`) have `field_type == "textarea"` before any write attempt — wrong type would silently truncate multi-line content.
2. Guards each contact's assembled output against unresolved `[placeholder]` text in the 10 email properties; call briefing properties (`task_note_1`, `task_note_2`) are intentionally exempt.
3. Writes all 12 properties per contact in batches of 100 via `client.crm.contacts.batch_api.update()`, decorated with `@retry(**HS_RETRY_KWARGS)` for 429/5xx resilience.
4. Writes a DLQ sentinel at startup; per-contact exceptions are routed to `write_dlq()` without halting the remaining batch.

Note creation and pin are added in Plan 06-02, which amends `main()`.

## Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create write_hubspot.py — schema check, bracket guard, batch property write, DLQ | c6516ad | scripts/write_hubspot.py |

## Acceptance Criteria — Verified

- [x] scripts/write_hubspot.py exists, AST-parses cleanly
- [x] All 5 functions present: `_check_property_schema`, `_bracket_guard`, `_build_batch_input`, `_write_properties_batch`, `main`
- [x] PROPERTY_MAP has exactly 12 entries (email_1_subject through email_5_body + task_note_1/2)
- [x] EMAIL_PROP_NAMES is a frozenset of exactly 10 entries (email_* only; task_note absent)
- [x] `_bracket_guard` raises ValueError on `[` in email properties; task_note_1/2 never scanned
- [x] `_check_property_schema` calls sys.exit(1) on wrong field_type; NOT decorated with @retry
- [x] `_write_properties_batch` decorated with `@retry(**HS_RETRY_KWARGS)`
- [x] `main()` calls `write_dlq` sentinel before the contact loop (line 131 vs loop line 145)
- [x] BATCH_SIZE = 100 present as module-level constant
- [x] Import block includes: hubspot, json, os, sys, tenacity.retry, utils.write_dlq, utils.HS_RETRY_KWARGS, BatchInputSimplePublicObjectBatchInput, SimplePublicObjectBatchInput

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — all 12 properties are wired from PROPERTY_MAP to assembled_{cid}.json keys. No placeholder values or hardcoded empty strings.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes beyond those documented in the plan's threat model.

All four threats from the plan's STRIDE register are mitigated as specified:

| Threat | Mitigation Applied |
|--------|--------------------|
| T-06-01 Tampering / bracket guard | EMAIL_PROP_NAMES covers all 10 fields (5 subjects + 5 bodies); task_note_* excluded |
| T-06-02 Tampering / schema check | `_check_property_schema` checks all 12 properties; sys.exit(1) on first mismatch |
| T-06-03 DoS / batch resilience | Per-contact try/except; failed contacts → DLQ; remaining contacts proceed |
| T-06-04 EoP / missing env var | `os.environ["HUBSPOT_API_KEY"]` raises KeyError at import time; fail-fast |

## Self-Check: PASSED

- [x] scripts/write_hubspot.py exists at expected path
- [x] Commit c6516ad confirmed in git log
- [x] AST parse: OK
- [x] All 5 required functions present
- [x] 12 PROPERTY_MAP entries confirmed
- [x] DLQ sentinel precedes loop (line 131 < line 145)
