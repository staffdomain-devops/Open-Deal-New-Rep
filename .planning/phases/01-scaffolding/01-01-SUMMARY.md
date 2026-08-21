---
phase: 01-scaffolding
plan: "01"
subsystem: infra
tags: [tenacity, hubspot-api-client, anthropic, requests, beautifulsoup4, python, retry, dlq]

requires: []
provides:
  - "scripts/utils.py — byte-for-byte copy of Inbound retry/DLQ helpers; exports write_dlq, HS_RETRY_KWARGS, REQ_RETRY_KWARGS, ANTHROPIC_RETRY_KWARGS, safe_truncate"
  - "requirements.txt — five pinned pip dependencies for all pipeline scripts"
affects: [02-record-assembly, 03-exclusion-routing, 04-generation, 05-lint-assembly, 06-write-back, 07-cicd]

tech-stack:
  added: [tenacity>=9.0.0, hubspot-api-client>=12.0.0, requests>=2.31.0, beautifulsoup4>=4.12.0, anthropic>=0.30.0]
  patterns:
    - "Retry with combined exponential backoff + Retry-After header (HubSpotRetryAfterWait ms, RetryAfterWait s)"
    - "DLQ sentinel written to RUNNER_TEMP/failed_contacts.json on failure"
    - "Shared utility module reused verbatim from Inbound — zero divergence policy"

key-files:
  created:
    - scripts/utils.py
    - requirements.txt
  modified: []

key-decisions:
  - "Reuse Inbound utils.py unchanged — same retry/DLQ patterns; no divergence needed (locked 2026-08-21)"
  - "Version pins use >= specifier throughout; == is explicitly rejected per SCAF-02"

patterns-established:
  - "All pipeline scripts import retry/DLQ helpers from scripts/utils.py"
  - "requirements.txt at project root; pip install -r requirements.txt installs all dependencies"

requirements-completed: [SCAF-01, SCAF-02]

duration: 2min
completed: 2026-08-21
---

# Phase 1 Plan 01: Copy utils.py + create requirements.txt Summary

**Retry/DLQ utility module copied byte-for-byte from Inbound (122 lines, diff clean) and requirements.txt created with five >= -pinned dependencies covering all pipeline scripts**

## Performance

- **Duration:** 2 min
- **Started:** 2026-08-21T01:25:45Z
- **Completed:** 2026-08-21T01:27:16Z
- **Tasks:** 2 completed
- **Files modified:** 2

## Accomplishments

- scripts/utils.py copied verbatim from Inbound — diff clean, Python import verified, all five public names resolve (write_dlq, safe_truncate, HS_RETRY_KWARGS, REQ_RETRY_KWARGS, ANTHROPIC_RETRY_KWARGS)
- requirements.txt created with exact five lines in specified order; exact-match assertion passed
- Both verification commands from the plan passed without error

## Task Commits

Each task was committed atomically:

1. **Task 1: Copy utils.py from Inbound verbatim (SCAF-01)** - `7982692` (feat)
2. **Task 2: Create requirements.txt with five pinned dependencies (SCAF-02)** - `096f2f2` (feat)

**Plan metadata:** _(to be committed with SUMMARY.md)_

## Files Created/Modified

- `scripts/utils.py` — Retry/DLQ helpers: write_dlq, RetryAfterWait, HubSpotRetryAfterWait, _hs_combined_wait, _anthropic_combined_wait, HS_RETRY_KWARGS, REQ_RETRY_KWARGS, ANTHROPIC_RETRY_KWARGS, safe_truncate
- `requirements.txt` — Five pinned dependencies: hubspot-api-client>=12.0.0, requests>=2.31.0, beautifulsoup4>=4.12.0, anthropic>=0.30.0, tenacity>=9.0.0

## Decisions Made

- Reuse Inbound utils.py unchanged: same retry/DLQ/backoff patterns; zero divergence policy locked in decisions log.
- Version pins use >= specifier (not == or ~=) per SCAF-02 requirement.

## Deviations from Plan

### Documentation Discrepancy (informational — not an auto-fix)

**Plan acceptance criteria states "Line count matches source: both files have 123 lines"**
- **Found during:** Task 1 verification
- **Actual state:** Both source (Inbound) and destination files are 122 lines; `diff` confirms byte-for-byte identity
- **Resolution:** The plan's line-count figure (123) is a documentation error; the verbatim copy requirement takes precedence and is satisfied — `diff` returns clean
- **No fix required:** The copy is correct; the spec figure is wrong by 1

---

**Total deviations:** 0 auto-fixes (1 informational note on spec line-count discrepancy)
**Impact on plan:** No impact — copy fidelity confirmed by clean diff; all import assertions passed.

## Issues Encountered

None — both tasks executed cleanly on first attempt.

## User Setup Required

None - no external service configuration required for this plan.

## Next Phase Readiness

- scripts/utils.py and requirements.txt are in place; any subsequent plan that imports utils will work.
- Plans 01-02 and 01-03 can proceed immediately (no dependency on this plan beyond file presence, which is now satisfied).
- Full pipeline install: `pip install -r requirements.txt` from project root.

## Known Stubs

None - no stubs. Both files are complete and functional.

## Threat Flags

None - copy fidelity confirmed by clean diff; no new network endpoints or trust boundaries introduced.

## Self-Check

- [x] `scripts/utils.py` exists and is importable
- [x] `requirements.txt` exists with exact 5 lines
- [x] Commit `7982692` exists (Task 1)
- [x] Commit `096f2f2` exists (Task 2)
- [x] `diff` of source vs destination returns clean

## Self-Check: PASSED

---
*Phase: 01-scaffolding*
*Completed: 2026-08-21*
