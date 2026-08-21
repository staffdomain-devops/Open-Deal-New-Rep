---
phase: 02-record-assembly
plan: 02
subsystem: api
tags: [hubspot, requests, tenacity, python, engagements, notes, handover]

# Dependency graph
requires:
  - phase: 02-record-assembly/02-01
    provides: fetch_list.py writing contact IDs to RUNNER_TEMP; utils.py with write_dlq, HS_RETRY_KWARGS, REQ_RETRY_KWARGS, safe_truncate
provides:
  - scripts/fetch_record.py with skeleton + six fetch functions covering all HubSpot data fetch logic
  - fetch_contact: contact props (12 fields) via SDK
  - fetch_company: company associations + company props (3 fields) via SDK
  - fetch_all_company_contacts: company-level contact associations + batch read + num_contacted_notes cast to int
  - fetch_deals: company deal associations + batch read + junk filter (test/delete token regex)
  - fetch_notes: v1 engagements pagination, bot-noise filter (8 prefixes), job-ad signal parsing, sensitive items detection (3 patterns)
  - fetch_handover: v1 engagements pagination, CALL vs outbound EMAIL comparison by timestamp, owner resolution, None fallback
affects: [02-record-assembly/02-03 — geo resolution and JSON assembly reads these functions; 03-exclusion-routing — reads contact_{id}.json produced by 02-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "v1 legacy engagements endpoint (/engagements/v1/engagements/associated/CONTACT/{id}/paged) for CALL/EMAIL/NOTE fetch with pagination via hasMore + offset"
    - "@retry(**HS_RETRY_KWARGS) on all SDK calls; @retry(**REQ_RETRY_KWARGS) on all requests calls"
    - "failed_step mutable tracking variable updated before each fetch call; write_dlq + sys.exit(1) on any exception"
    - "DLQ sentinel written at script startup before any fetch calls"
    - "Bot-noise filter checks note_body[:80] against BOT_NOISE_PREFIXES; JOB_AD_PREFIXES subset drives live_hiring_signals parsing"
    - "Sensitive items detection: 3 regex patterns (negative language, competitor mention, explicit marker); false-positive bias"

key-files:
  created:
    - scripts/fetch_record.py
  modified: []

key-decisions:
  - "Tasks 1 and 2 implemented in one atomic file write — separate commits not possible for new file creation; combined commit 01dae1b covers both task deliverables"
  - "fetch_notes fetches engagements for contact + top-2 colleagues (num_contacted_notes >= 1, sorted desc)"
  - "_fetch_engagements_paged is a private helper reused by both fetch_notes and fetch_handover"
  - "fetch_company returns ('', {}) if no company association; all downstream functions guard on empty company_id"
  - "num_contacted_notes cast to int in fetch_all_company_contacts (HubSpot returns string)"
  - "_id stored on each colleague dict to enable v1 endpoint calls in fetch_notes"

patterns-established:
  - "Private _fetch_engagements_paged helper wraps v1 paged endpoint with @retry inner function"
  - "Private helper functions (_parse_hiring_signal, _is_sensitive) keep fetch_notes readable"
  - "Module-level constants for all property lists and prefix arrays"

requirements-completed: [FETCH-02, FETCH-03, FETCH-04, FETCH-05, FETCH-06, FETCH-07]

# Metrics
duration: 3min
completed: 2026-08-21
---

# Phase 2 Plan 02: fetch_record.py Core Fetch Functions Summary

**Six HubSpot fetch functions (contact, company, company-contacts, deals, bot-noise-filtered notes with signals + sensitive-item detection, handover resolution) implemented in fetch_record.py with DLQ sentinel and failed-step tracking**

## Performance

- **Duration:** ~3 min
- **Started:** 2026-08-21T06:35:01Z
- **Completed:** 2026-08-21T06:37:20Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments

- fetch_record.py created with skeleton: all 6 constant lists (CONTACT_PROPS 12 fields, COMPANY_PROPS, COLLEAGUE_PROPS, DEAL_PROPS, BOT_NOISE_PREFIXES 8, JOB_AD_PREFIXES 5), env reads, SDK client, DLQ sentinel, main() with failed_step tracking
- All six fetch functions implemented: fetch_contact, fetch_company, fetch_all_company_contacts, fetch_deals, fetch_notes, fetch_handover
- fetch_notes applies full bot-noise filter, job-ad signal parsing, and three-pattern sensitive items detection; fetches contact + top-2 colleagues
- fetch_handover compares max CALL timestamp vs max outbound EMAIL timestamp, resolves owner via SDK, returns None if both absent
- All spec verification checks pass: v1 endpoint URL, hasMore pagination, direction == "EMAIL", \btest\b junk filter, sensitive_items, JOB_AD_PREFIXES

## Task Commits

Each task was committed atomically:

1. **Task 1: Script skeleton** - `01dae1b` (feat) — Note: Tasks 1 and 2 delivered in the same commit since both target the same new file; the commit satisfies all acceptance criteria for both tasks

**Plan metadata:** (to follow — docs commit)

## Files Created/Modified

- `scripts/fetch_record.py` — Full fetch script: constants, SDK client, 6 fetch functions, private helpers, main() with DLQ sentinel and failed_step tracking

## Decisions Made

- Tasks 1 and 2 are both implemented in the single file creation. Since git cannot split a new-file write across two commits meaningfully, a single commit covers both task deliverables. All acceptance criteria for Task 1 (skeleton) and Task 2 (six fetch functions) are verified independently.
- `_fetch_engagements_paged` extracted as a private helper (not in the plan spec) to avoid duplicating v1 endpoint pagination logic between fetch_notes and fetch_handover. This is a Rule 2 structural clarity choice — no behavior difference.
- `_id` stored on colleague dicts so fetch_notes can call the v1 endpoint for each colleague without a second association lookup.
- fetch_company returns `("", {})` on no association; all downstream functions guard on empty string rather than None for consistency.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical] Added `_id` field to colleague dicts**
- **Found during:** Task 2 (fetch_notes implementation)
- **Issue:** fetch_notes needs the HubSpot contact ID of each colleague to call the v1 engagements endpoint; the plan spec for fetch_all_company_contacts did not include the ID in the returned dict
- **Fix:** Added `"_id": str(item.id)` to each colleague dict; filtered out when building story_notes (not passed to the Phase 3 JSON schema — will be stripped in 02-03 assembly)
- **Files modified:** scripts/fetch_record.py
- **Verification:** fetch_notes selects colleague IDs via `c["_id"]` without additional API call
- **Committed in:** 01dae1b

**2. [Rule 2 - Missing Critical] Extracted `_fetch_engagements_paged` shared helper**
- **Found during:** Task 2 (fetch_handover implementation — same pagination pattern needed)
- **Issue:** Both fetch_notes and fetch_handover require v1 engagements pagination; inline duplication would risk divergence on retry/pagination logic
- **Fix:** Extracted private `_fetch_engagements_paged(person_id)` helper with `@retry(**REQ_RETRY_KWARGS)` on inner `_get_page`; both functions call it
- **Files modified:** scripts/fetch_record.py
- **Verification:** Both functions use the same pagination path; REQ_RETRY_KWARGS applied correctly
- **Committed in:** 01dae1b

---

**Total deviations:** 2 auto-fixed (both Rule 2 — missing critical functionality for correctness)
**Impact on plan:** Both additions necessary for the fetch_notes → colleague-notes flow and for DRY pagination. No scope creep; no behavior change vs. spec intent.

## Issues Encountered

None — all HubSpot SDK calls, v1 endpoint patterns, and regex patterns implemented correctly on first pass.

## Known Stubs

- `main()` prints a status line but does not write `contact_{id}.json` — intentional; JSON assembly is Plan 02-03's job per the plan spec.

## Threat Flags

No new threat surface introduced beyond what the threat model in 02-02-PLAN.md covers. All five STRIDE mitigations are implemented:
- T-02-05: sensitive_items detection present with 3-pattern check
- T-02-06: HS_RETRY_KWARGS on all SDK batch calls
- T-02-07: REQ_RETRY_KWARGS via _fetch_engagements_paged inner function
- T-02-08: failed_step tracking + write_dlq + sys.exit(1) on any exception
- T-02-09: HUBSPOT_API_KEY not interpolated into error strings

## Next Phase Readiness

- Plan 02-03 can import fetch_record.py directly and extend main() with: geo resolution (D-06 ladder), departure_flagged check (D-14), is_only_contact flag (D-11), and final JSON assembly writing contact_{id}.json
- All six fetch functions are module-level and can be called independently for testing
- The _id key on colleague dicts will be stripped during JSON assembly in 02-03 (it is not in the D-13 schema)

---
*Phase: 02-record-assembly*
*Completed: 2026-08-21*
