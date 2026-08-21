---
phase: 01-scaffolding
plan: "02"
subsystem: config
tags: [routing, close-bank, deterministic, python]
dependency_graph:
  requires: []
  provides: [config.vertical_routing.route, config.vertical_routing.VerticalRoute, config.close_bank.assign_close, config.close_bank.CLOSE_BANK]
  affects: [scripts/exclude_and_route.py (Phase 3)]
tech_stack:
  added: []
  patterns: [dataclass, module-level mutable counter for rotation]
key_files:
  created:
    - config/__init__.py
    - config/vertical_routing.py
    - config/close_bank.py
  modified: []
decisions:
  - industry_match_strength not included in VerticalRoute — computed by assign_close caller, not route()
  - _close_counter stored as list[int] to enable mutation without `global` keyword
  - Default routing row uses tokens=[] and always appears last — iteration logic matches empty token list as unconditional default
metrics:
  duration_minutes: 5
  completed_date: "2026-08-21"
  tasks_completed: 2
  tasks_total: 2
  files_created: 3
  files_modified: 0
---

# Phase 1 Plan 02: Vertical Routing Table + Close Bank Summary

**One-liner:** 14-row case-insensitive substring routing table and 5-option JP-verbatim close bank with touches/C-suite/exact-match priority assignment.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Implement config/vertical_routing.py — 14-row routing table (SCAF-03) | fd5fb12 | config/__init__.py, config/vertical_routing.py |
| 2 | Implement config/close_bank.py — 5-option close bank with assignment logic (SCAF-04) | 4db1144 | config/close_bank.py |

## What Was Built

### config/vertical_routing.py

Exports `VerticalRoute` (frozen dataclass with `case_study`, `email3_url`, `email4_url`) and `route(industry: str) -> VerticalRoute`.

- `ROUTING_TABLE`: 14 entries — 13 named verticals + 1 default row (tokens=[], always fires last).
- Matching: `industry.lower().strip()`, then iterate rows top-to-bottom checking if any token is a substring of the normalised input. First match wins.
- `route(None)` and `route("")` both return the Bells Pure Ice default row without raising.
- No URL in the table contains `offshore`, `outsourc`, or `bpo` slugs.

### config/close_bank.py

Exports `CLOSE_BANK` (dict 1–5 with JP's verbatim close text) and `assign_close(touches, jobtitle, industry_match_strength) -> tuple[int, str]`.

Priority order (highest first):
1. `touches >= 30` → option 3
2. C-suite substring in jobtitle (CEO/MD/COO/CFO/Partner/Principal/Director, case-insensitive) → option 4
3. `industry_match_strength == "exact"` → option 5
4. Fallback: rotate 1→2→1→2 via `_close_counter[0]`

## Verification Results

All plan-level and task-level assertions passed:

```
vertical_routing OK
close_bank OK
```

Acceptance criteria verified:
- ROUTING_TABLE has exactly 14 entries
- VerticalRoute has exactly 3 fields: case_study, email3_url, email4_url
- route("") and route(None) return Bells Pure Ice without raising
- route("Staffing & Recruitment") returns Cox Purtell (row 2 wins over row 10)
- CLOSE_BANK has exactly 5 keys with verbatim JP wording
- assign_close(35, "CEO", "partial") returns option 3 (touches priority over C-suite)
- Rotation 1→2→1 confirmed across three calls with counter reset to 0

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. Both modules are fully wired with deterministic logic and no placeholder values.

## Threat Flags

No new threat surface introduced. Both modules are pure functions operating on trusted pipeline data (route() on untrusted free text; assign_close() on upstream-computed values). Threat register items T-02-01 and T-02-02 mitigated:
- T-02-01: CLOSE_BANK wording asserted via start-of-string checks in acceptance criteria
- T-02-02: No banned URL slugs present in ROUTING_TABLE (verified programmatically)

## Self-Check

Files exist:
- config/__init__.py: FOUND
- config/vertical_routing.py: FOUND
- config/close_bank.py: FOUND

Commits exist:
- fd5fb12: FOUND
- 4db1144: FOUND

## Self-Check: PASSED
