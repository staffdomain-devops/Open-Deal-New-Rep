---
phase: 06-write-back
plan: 02
subsystem: write-back
tags: [hubspot, notes-api, associations-api, pin, manual-fallback, tenacity]
dependency_graph:
  requires:
    - 06-01-SUMMARY.md  # write_hubspot.py with schema check + bracket guard + batch write
  provides:
    - scripts/write_hubspot.py  # amended: _create_note, _pin_note, updated main() loop
  affects:
    - HubSpot contact records (1 pinned note per contact; manual_pin_list.json fallback)
tech_stack:
  added: []
  patterns:
    - "@retry(**HS_RETRY_KWARGS) on _create_note (consistent with _write_properties_batch pattern)"
    - "_pin_note catches all exceptions internally; never raises; read-modify-write on manual_pin_list.json"
    - "note-contact association via associations_api.create() immediately after note creation"
    - "assembled['pin']['body'] sourced directly from assembled_{cid}.json"
key_files:
  created: []
  modified:
    - scripts/write_hubspot.py
decisions:
  - "_create_note decorated with @retry: note creation is a transient-retriable network call; exhausted retries bubble to main() DLQ"
  - "_pin_note NOT decorated with @retry: pin failure is a soft non-fatal fallback; retrying a potentially-unsupported API endpoint serves no purpose"
  - "manual_pin_list.json uses read-modify-write (same pattern as _write_to_review_sample in lint.py): safe for single-process execution"
  - "_pin_note failure does not write to DLQ: pin is advisory; contact properties are already written; manual recovery via manual_pin_list.json is sufficient"
metrics:
  duration_seconds: 120
  completed_date: "2026-08-27"
  tasks_completed: 1
  tasks_total: 1
  files_created: 0
  files_modified: 1
---

# Phase 6 Plan 02: Note Creation, Contact Association, and Pin-with-Manual-Fallback Summary

**One-liner:** HubSpot note creation with hs_note_body + hs_timestamp, note-to-contact association, and pin attempt with read-modify-write fallback to manual_pin_list.json on failure.

## What Was Built

`scripts/write_hubspot.py` amended to add the second half of the write-back stage:

1. `_create_note(client, contact_id, body)` — creates a HubSpot note object via `client.crm.objects.notes.basic_api.create()` with `hs_note_body` and `hs_timestamp` properties, immediately associates it with the contact via `associations_api.create()`, and returns the `note_id` string. Decorated with `@retry(**HS_RETRY_KWARGS)` for 429/5xx resilience; exhausted retries bubble to `main()`'s per-contact `except` block.

2. `_pin_note(contact_id, note_id, client)` — attempts to set `hs_pinned_engagement_id` on the contact via `client.crm.contacts.basic_api.update()`. On success returns `True`. On any exception, appends `{"contact_id": str, "note_id": str}` to `manual_pin_list.json` in `RUNNER_TEMP` using read-modify-write (load existing list or `[]`, append, `json.dump` back), prints a warning to stderr, and returns `False`. Never raises. Not decorated with `@retry`.

3. `main()` loop amended — inside the per-contact `try` block, after batch accumulation and optional flush:
   ```python
   note_id = _create_note(client, str(cid), assembled["pin"]["body"])
   _pin_note(str(cid), note_id, client)
   ```
   The `failed_step` string in the `except` block updated from `"write_properties"` to `"write_properties_or_note"` to reflect expanded scope.

The 5 functions from Plan 06-01 are unchanged (`_check_property_schema`, `_bracket_guard`, `_build_batch_input`, `_write_properties_batch`, and the non-loop parts of `main`).

## Tasks

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add _create_note and _pin_note; amend main() to call them per contact | dfb8ed4 | scripts/write_hubspot.py |

## Acceptance Criteria — Verified

- [x] scripts/write_hubspot.py parses cleanly (AST parse: OK)
- [x] All 7 functions present: 5 from 06-01 + `_create_note` + `_pin_note`
- [x] `_create_note` decorated with `@retry(**HS_RETRY_KWARGS)`; creates note with `hs_note_body` + `hs_timestamp`; associates with contact; returns `note_id` string
- [x] `_pin_note` NOT decorated with `@retry`; catches all exceptions internally; appends to `manual_pin_list.json` using read-modify-write on failure; returns bool
- [x] `main()` loop calls `_create_note` then `_pin_note` for each contact inside per-contact try block
- [x] `manual_pin_list.json` path constructed as `os.path.join(RUNNER_TEMP, "manual_pin_list.json")`
- [x] `_pin_note` failure does not raise and does not write to DLQ
- [x] The 5 functions from Plan 06-01 are unchanged

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None — `assembled["pin"]["body"]` is wired directly from the assembled JSON produced by `assemble_bodies.py`. No placeholder values.

## Threat Flags

None — no new network endpoints, auth paths, or schema changes beyond those documented in the plan's threat model.

STRIDE mitigations applied as specified:

| Threat | Disposition | Mitigation Applied |
|--------|-------------|-------------------|
| T-06-01 Tampering / bracket guard | mitigate (inherited) | `_bracket_guard` runs before note body is read; pin body sourced from `assembled["pin"]["body"]` without modification |
| T-06-02 Tampering / schema check | mitigate (inherited) | `_check_property_schema` still runs at startup before any note creation |
| T-06-03 DoS / _create_note | mitigate | `@retry(**HS_RETRY_KWARGS)` on `_create_note`; exhausted retries caught by main() DLQ |
| T-06-04 Availability / _pin_note | accept | Fallback to `manual_pin_list.json`; non-fatal; pilot verification will confirm pinning support |

## Self-Check: PASSED

- [x] scripts/write_hubspot.py exists and was modified
- [x] Commit dfb8ed4 confirmed in git log
- [x] AST parse: OK
- [x] All 7 required functions present (confirmed by plan's automated verify command)
- [x] `manual_pin_list.json` referenced in 3 locations
- [x] `hs_note_body` present in note creation properties
- [x] `_pin_note` has no `@retry` decorator (verified by grep -A2)
- [x] `_create_note` has `@retry(**HS_RETRY_KWARGS)` immediately before `def` (verified by grep -B2)
