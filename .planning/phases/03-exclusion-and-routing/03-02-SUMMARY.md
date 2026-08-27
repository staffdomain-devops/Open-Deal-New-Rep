---
phase: 03-exclusion-and-routing
plan: 02
subsystem: routing
tags: [python, vertical-routing, close-bank, brief-assembly, json, section-3.7]

# Dependency graph
requires:
  - phase: 03-exclusion-and-routing/03-01
    provides: exclude_and_route.py exclusion stage, passing list, records dict

provides:
  - route_contact() — vertical routing + industry_match_strength
  - _get_colleagues() — top-2 engaged colleagues excluding contact
  - build_brief() — full §3.7 brief text (14 labels, all conditional branches)
  - main() extension — route_contact → assign_close → build_brief → brief_{id}.json per passing contact

affects: [04-generation]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "industry_match_strength determined by re-scanning ROUTING_TABLE after route() call (avoids double call)"
    - "GEO_DISPLAY module-level constant maps geo codes to display strings including Australia-assumption warning for US/UK"
    - "Per-contact routing errors caught in try/except; write_dlq called; batch continues (T-03-07)"
    - "INTERNAL and LIVE HIRING SIGNALS sections omitted entirely (not empty label) when source lists are empty"

key-files:
  created: []
  modified:
    - scripts/exclude_and_route.py

key-decisions:
  - "industry_match_strength 'exact' only when a named row with tokens matched; 'partial' when default row fired — drives assign_close priority 3"
  - "Notes truncated at first line (300 chars max) in brief WHAT THE NOTES SAY — keeps brief scannable for reps"
  - "COLLEAGUES block falls back to 'none on record' (not omitted) when is_only_contact=False but no colleagues qualify — avoids misleading silence"

requirements-completed:
  - ROUTE-01
  - ROUTE-02
  - ROUTE-03
  - ROUTE-04
  - ROUTE-05

# Metrics
duration: <5min
completed: 2026-08-27
---

# Phase 3 Plan 02: Routing & Brief Assembly — Summary

**exclude_and_route.py extended: route_contact, _get_colleagues, build_brief added; main() now writes brief_{id}.json per passing contact in exact §3.7 format.**

## Performance

- **Duration:** ~5 min
- **Completed:** 2026-08-27
- **Tasks:** 3 of 3 complete
- **Files modified:** 1 (exclude_and_route.py)
- **Commits:** `ac1a94e`

## Accomplishments

**Task 1 — route_contact() + _get_colleagues():**
- `route_contact(record)` calls `route(industry)` then re-scans `ROUTING_TABLE` to determine `industry_match_strength` ("exact" if any named-row token matched; "partial" if default row fired)
- `_get_colleagues(contact_props, all_company_contacts)` filters by `num_contacted_notes >= 1`, excludes the contact by firstname+lastname (case-insensitive), sorts descending, returns top 2

**Task 2 — build_brief():**
- All 14 §3.7 spec labels present verbatim in spec-locked field order
- GEO_DISPLAY maps AU/NZ/US/UK including "Nothing in the copy may assume Australia." instruction for US/UK
- No-deal branch: "No previous deal on record. Do not invent one." (ROUTE-04)
- Only-contact branch: "The recipient is the only contact on this account. Do not reference colleagues." (ROUTE-05)
- INTERNAL and LIVE HIRING SIGNALS sections omitted entirely when source lists are empty (not rendered with empty content)
- Notes: first line per note, capped at 300 chars, up to 5 notes

**Task 3 — main() wiring:**
- For each passing contact: `route_contact` → `assign_close` → `build_brief` → `json.dump(brief_payload)`
- `brief_payload` contains all 7 required keys: `contact_id`, `brief_text`, `case_study`, `email3_url`, `email4_url`, `close_option`, `close_text`
- `brief_{cid}.json` written to `RUNNER_TEMP`
- Per-contact errors DLQ-logged and skipped without aborting batch (T-03-07)
- Final print: `"Briefs written: {n}/{total passing}"`

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|------------|
| T-03-06 | sensitive_items tagged with "INTERNAL - NEVER REFERENCE:"; system prompt enforcement in Phase 4 |
| T-03-07 | Per-contact try/except; write_dlq on failure; batch continues |
| T-03-09 | LIVE HIRING SIGNALS block conditionally omitted when list is empty |
| T-03-10 | All 14 spec label strings verified present in acceptance criteria |

## Deviations from Plan

None.

---
*Phase: 03-exclusion-and-routing*
*Completed: 2026-08-27*
