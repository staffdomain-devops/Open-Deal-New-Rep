---
phase: 04-generation
plan: 02
subsystem: generation-batch
tags: [python, anthropic, batch-api, polling, custom-id, dlq]

# Dependency graph
requires:
  - phase: 04-generation/04-01
    provides: generate_campaign.py with realtime path, parse_output, write_generated, SYSTEM_MESSAGE

provides:
  - scripts/generate_campaign.py (amended: batch path added — submit_batch, poll_batch, process_batch_results)
  - INPUT_USE_BATCH_API toggle in main() replacing sys.exit(1) stub

affects: [05-lint, 06-writeback]

# Tech tracking
tech-stack:
  added:
    - anthropic SDK batch path (client.messages.batches.create / retrieve / results — GA namespace)
  patterns:
    - "custom_id=contact_id on every batch request; results always matched by item.custom_id (never positional)"
    - "poll_batch sleeps 60s between retrieve() calls; exits when processing_status == ended"
    - "process_batch_results: succeeded branch calls parse_output+write_generated; all other types call write_dlq"
    - "GA namespace enforced: client.messages.batches (never client.beta.messages.batches)"

key-files:
  modified:
    - scripts/generate_campaign.py

key-decisions:
  - "Batch path replaces sys.exit(1) stub in main(); realtime loop unchanged"
  - "submit_batch loads briefs inline (dict keyed by cid); passes brief_text as user content"
  - "No @retry on poll_batch.retrieve() — polling loop provides its own iteration"
  - "Non-succeeded batch result types (errored, canceled, expired) all call write_dlq with result.type in error_message"

requirements-completed:
  - GEN-04

# Metrics
duration: <2min
completed: 2026-08-27
---

# Phase 4 Plan 02: Generation Batch Path — Summary

**generate_campaign.py extended with three batch functions (submit_batch, poll_batch, process_batch_results) and INPUT_USE_BATCH_API toggle replacing the sys.exit(1) stub; realtime path unchanged.**

## Performance

- **Duration:** ~2 min
- **Completed:** 2026-08-27
- **Tasks:** 1 of 1 complete
- **Files modified:** 1 (generate_campaign.py)
- **Commits:** `4867407` (included in Wave 1 commit — both plans implemented in single pass)

## Accomplishments

**generate_campaign.py batch additions (Task 1):**
- `submit_batch(contact_ids, briefs)`: builds requests list with `custom_id=cid` and params (`model`, `max_tokens`, `system=SYSTEM_MESSAGE`, `messages`); calls `client.messages.batches.create`; returns `batch.id`
- `poll_batch(batch_id)`: while-True loop calling `client.messages.batches.retrieve`; prints status line each iteration; exits when `processing_status == "ended"`; returns final batch object
- `process_batch_results(batch_id, briefs)`: iterates `client.messages.batches.results`; matches by `item.custom_id`; on `succeeded` calls `parse_output + write_generated`; on any other type calls `write_dlq` with `result.type` in error message
- `main()` batch branch: loads all briefs into dict keyed by cid (skipping missing files); calls `submit_batch → poll_batch → process_batch_results`

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|------------|
| T-04-07 | Always matched by item.custom_id (never positional index) |
| T-04-10 | Every non-succeeded result type (errored, canceled, expired) calls write_dlq; none silently discarded |
| T-04-11 | GA namespace enforced; grep -c "client.beta" returns 0 |

## Deviations from Plan

Both Wave 1 and Wave 2 implemented in a single pass and committed together (`4867407`). The batch path was designed alongside the realtime path since both target the same file; no logical dependency was violated.

---
*Phase: 04-generation*
*Completed: 2026-08-27*
