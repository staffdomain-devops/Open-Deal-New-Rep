---
phase: 03-exclusion-and-routing
plan: 01
subsystem: exclusion
tags: [python, exclusion-filters, hubspot, json, e1-e6, geo-hold, dlq]

# Dependency graph
requires:
  - phase: 02-record-assembly/02-03
    provides: contact_{id}.json with geo, handover, contact_props, company_id fields

provides:
  - scripts/exclude_and_route.py (exclusion filter stage: E1–E6 + geo-hold)
  - exclusion_report.json (list of excluded/held contacts with filter_code + reason)
  - passing list returned from main() for Plan 03-02 routing

affects: [03-02-routing]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Geo hold runs before E-filters (not a spec filter code; data error not list quality issue)"
    - "E4 dedup uses mutable seen: dict passed by reference across contacts (avoids global)"
    - "exclusion_report.json always written even if empty list (EXCL-07)"
    - "load errors appended to excluded list with LOAD_ERROR code; batch continues"

key-files:
  created:
    - scripts/exclude_and_route.py
  modified:
    - scripts/fetch_record.py

key-decisions:
  - "hs_email_bounce added to CONTACT_PROPS so E6 bounced check has data"
  - "owner_id added to handover dict so E1/E2 owner comparison is possible"
  - "company_id promoted to top-level D-13 key so E4 email+company dedup works"
  - "filter order locked: geo_hold → E1 → E2 → E3 → E4 → E5 → E6 (stop at first hit per spec §3.5)"

requirements-completed:
  - EXCL-01
  - EXCL-02
  - EXCL-03
  - EXCL-04
  - EXCL-05
  - EXCL-06
  - EXCL-07

# Metrics
duration: <5min
completed: 2026-08-27
---

# Phase 3 Plan 01: Exclusion Filters — Summary

**fetch_record.py amended (3 surgical additions); exclude_and_route.py created with E1–E6, geo-hold, exclusion_report.json write, and passing list for routing.**

## Performance

- **Duration:** ~5 min
- **Completed:** 2026-08-27
- **Tasks:** 2 of 2 complete
- **Files modified:** 1 (fetch_record.py); **Files created:** 1 (exclude_and_route.py)
- **Commits:** `1298ea7`

## Accomplishments

**fetch_record.py amendments (Task 1):**
- `hs_email_bounce` added to `CONTACT_PROPS` list (after `phone`) — enables E6 bounce check
- `owner_id` added to `fetch_handover()` return dict (after `is_active`) — enables E1/E2 owner comparison
- `company_id` promoted to top-level key in D-13 record dict (after `contact_props`) — enables E4 dedup; D-13 now has 12 keys (up from 11)

**exclude_and_route.py (Task 2):**
- All 10 functions: `load_record`, `_parse_date`, `check_e1`–`check_e6`, `check_geo_hold`, `main`
- All 9 filter codes: `E1_OWNER_UNCHANGED`, `E2_PREV_OWNER_STILL_ACTIVE`, `E3_RECENT_ACTIVITY`, `E4_DUPLICATE_OF_{id}`, `E5_NO_ENGAGEMENT_HISTORY`, `E6_UNSUBSCRIBED`, `E6_BOUNCED`, `E6_INVALID_EMAIL`, `GEO_UNRESOLVED`
- DLQ sentinel written at startup before any processing (ERR-02)
- `exclusion_report.json` always written via `json.dump`, even when excluded list is empty (EXCL-07)
- `main()` returns `(passing, records)` tuple for Plan 03-02 to extend

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|------------|
| T-03-01 | DLQ sentinel at startup captures crash before report write |
| T-03-02 | E1/E2 both sides cast to str() before comparison |
| T-03-03 | `_parse_date` returns None on failure; None does not trigger E3 |

## Deviations from Plan

None.

---
*Phase: 03-exclusion-and-routing*
*Completed: 2026-08-27*
