---
phase: 02-record-assembly
plan: 03
subsystem: api
tags: [hubspot, python, geo-resolution, departure-check, json-assembly, d13-schema]

# Dependency graph
requires:
  - phase: 02-record-assembly/02-02
    provides: fetch_contact, fetch_company, fetch_all_company_contacts, fetch_deals, fetch_notes, fetch_handover in scripts/fetch_record.py

provides:
  - resolve_geo (3-step ladder: company.country → phone prefix → UNRESOLVED)
  - check_departure (story notes regex against contact name within 30 chars)
  - is_only_contact_check (sole-contacted-person detection with empty-associations fallback)
  - D-13 assembly: 11-key record dict written to $RUNNER_TEMP/contact_{id}.json via json.dump

affects: [03-exclusion-routing, 04-generation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "All-or-nothing JSON write: json.dump only reached after every fetch + resolution call succeeds (D-15, T-02-10)"
    - "3-step geo ladder: country string match → phone prefix → UNRESOLVED fallback with stderr warning"
    - "Regex departure check bounded to 30-char window around contact name (T-02-12 backtracking safety)"

key-files:
  created: []
  modified:
    - scripts/fetch_record.py

key-decisions:
  - "resolve_geo returns UNRESOLVED string (not null/empty) so Phase 3 exclusion filter can pattern-match cleanly"
  - "is_only_contact fallback on empty all_company_contacts returns True (conservative safe-overstatement)"
  - "json.dump uses default=str for non-serialisable types (datetime from SDK); no custom serialiser needed"

patterns-established:
  - "Resolution functions placed in their own section between fetch functions and main() for clear separation"
  - "failed_step tracking extended to resolution calls; DLQ always called before sys.exit(1)"

requirements-completed:
  - FETCH-08
  - FETCH-09
  - FETCH-10
  - FETCH-11

# Metrics
duration: 2min
completed: 2026-08-21
---

# Phase 2 Plan 03: Record Assembly — Geo, Departure, Only-Contact, D-13 Assembly Summary

**fetch_record.py completed: resolve_geo (3-step ladder), check_departure (30-char regex), is_only_contact_check, and 11-key D-13 JSON write to RUNNER_TEMP**

## Performance

- **Duration:** ~2 min
- **Started:** 2026-08-21T06:41:07Z
- **Completed:** 2026-08-21T06:42:56Z
- **Tasks:** 1 of 2 complete (Task 2 is a human-verify checkpoint — awaiting real-run approval)
- **Files modified:** 1

## Accomplishments

- `resolve_geo`: Step 1 matches 8 country string variants (case-insensitive) across AU/NZ/US/UK; Step 2 strips spaces+dashes then matches phone prefix; Step 3 returns "UNRESOLVED" with stderr warning
- `check_departure`: Searches "has left" / "no longer with" / "moved on from" within 30 chars of contact firstname or lastname (case-insensitive); gracefully returns False when both names are empty
- `is_only_contact_check`: Returns True when exactly one contacted colleague matches by firstname+lastname; returns True when `all_company_contacts` is empty (conservative fallback, D-14)
- `main()` extended: resolution calls follow all six fetch calls with `failed_step` tracking; D-13 dict assembled with all 11 keys in spec order; `contact_{id}.json` written via `json.dump(record, f, indent=2, default=str)` — only reached after all calls succeed (T-02-10 no-partial-write guarantee)
- Stub print line from Plan 02-02 removed; module docstring updated

## Task Commits

1. **Task 1: resolve_geo, check_departure, is_only_contact_check, D-13 assembly** - `0232564` (feat)
2. **Task 2: human-verify checkpoint** - Pending user approval after real-run test

**Plan metadata:** `[pending — added after checkpoint approval]`

## Files Created/Modified

- `scripts/fetch_record.py` — Complete per-contact assembly script with all 10 functions and D-13 JSON write

## Decisions Made

- `resolve_geo` returns `"UNRESOLVED"` as a non-empty string so Phase 3 exclusion filter E5 can match on the exact value without null checks
- `is_only_contact_check` empty-list fallback returns `True` (conservative overstatement) per D-14; Phase 3 will say "only contact", which is safe vs. silently missing the flag
- `json.dump` uses `default=str` — handles HubSpot SDK datetime objects without a custom serialiser; aligns with the pattern established in 02-02 context

## Deviations from Plan

None — plan executed exactly as written.

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|------------|
| T-02-10 | json.dump inside try block, only reached after all fetch + resolution calls succeed; exception before dump leaves no partial file |
| T-02-11 | resolve_geo always returns "UNRESOLVED" string; Phase 3 exclusion filter handles hold |
| T-02-12 | check_departure patterns bounded to 30-char window; safe_truncate(body, 5000) applied by fetch_notes before notes reach check_departure |
| T-02-14 | is_only_contact_check returns True on empty all_company_contacts (conservative fallback) |

## Known Stubs

None — all 11 D-13 fields are computed from real fetch results; no hardcoded placeholders.

## Checkpoint Status

**Task 2 (checkpoint:human-verify) is pending.** The script requires `HUBSPOT_API_KEY` to run against a real contact. Once the key is available, run:

```powershell
$env:HUBSPOT_API_KEY = "your-key-here"
$env:RUNNER_TEMP = "."
python scripts/fetch_record.py <contact_id>
```

Then verify `contact_{id}.json` with:
```python
python -c "import json; d=json.load(open('contact_<id>.json')); expected={'contact_props','company_props','all_company_contacts','deals','story_notes','live_hiring_signals','handover','geo','is_only_contact','sensitive_items','departure_flagged'}; missing=expected-set(d); print('MISSING:',missing) if missing else print('All 11 keys present')"
```

## Next Phase Readiness

- `scripts/fetch_record.py` is functionally complete and syntactically valid
- All 10 functions present; D-13 schema wired; no partial write on failure
- Pending: human verification of real `contact_{id}.json` output (checkpoint Task 2)
- Once checkpoint approved: Phase 2 is complete; Phase 3 (exclusion & routing) can begin

---
*Phase: 02-record-assembly*
*Completed: 2026-08-21 (Task 1 complete; Task 2 checkpoint pending)*
