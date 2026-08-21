# State — Lane A Owner-Changed Re-Engagement Pipeline

## Project Reference

See: `.planning/PROJECT.md` (updated 2026-08-21)

**Core value:** Every new account owner inherits a credible, personalised handover sequence built from the actual file.
**Current focus:** Phase 1 — Scaffolding

## Current Phase

**Phase 1: Scaffolding — Executing**
Status: Wave 1 in progress — 2/3 plans complete (01-01, 01-02 done)

## Phase History

| Phase | Status | Notes |
|-------|--------|-------|
| 1 | Executing | 01-01, 01-02 complete; 01-03 pending |
| 2 | Not started | Record Assembly |
| 3 | Not started | Exclusion & Routing |
| 4 | Not started | Generation |
| 5 | Not started | Lint & Body Assembly |
| 6 | Not started | Write-back |
| 7 | Not started | CI/CD |

## Open Questions

- What is the geo resolution ladder from Lane B v2.1 §3.6.1? (Referenced in v1.1 amendment but Lane B spec not in this repo — need JP to confirm or provide Lane B spec)
- Is note-pinning supported via the HubSpot engagements API? (Checklist item 14 says verify at pilot — fallback is manual pin pass)
- Which sign-off substitution method for `[Rep first name]`: sequence token replaces it, or write-back substitutes the sender's real first name before writing? Decision needed from JP before Phase 6.
- Reply-kill workflow (unenrol sequence + close call tasks on inbound reply) — is this a HubSpot workflow or a daily sweep script? Out of scope v1 but needs to be in place before full-list run.

## Decisions Log

| Date | Decision | Context |
|------|----------|---------|
| 2026-08-21 | Batch list-driven, not per-contact | Volume + exclusion report review required before send |
| 2026-08-21 | Reuse Inbound utils.py unchanged | Same retry/DLQ patterns; no divergence |
| 2026-08-21 | One call per contact, all 8 deliverables | Model needs arc visibility; call notes reference email content |
| 2026-08-21 | Cached system prompt | Long verbatim prompt amortised across all contacts in run |
| 2026-08-21 | Call tasks created manually by rep | Pipeline writes call briefings to `task_note_1` + `task_note_2` contact properties; rep creates HubSpot tasks themselves |
| 2026-08-21 | Version pins use >= specifier throughout | == rejected per SCAF-02; >= allows patch updates without pipeline changes |
| 2026-08-21 | utils.py zero-divergence policy locked | Byte-for-byte copy confirmed by clean diff; no pipeline-specific modifications permitted |
| 2026-08-21 | industry_match_strength not in VerticalRoute | Computed by assign_close() caller in Phase 3; route() returns only case_study + URLs |
| 2026-08-21 | _close_counter as list[int] | Mutable single-element list enables rotation without `global` keyword; module-level state |

## Last Session

- **Timestamp:** 2026-08-21T01:34:00Z
- **Stopped at:** Completed 01-02-PLAN.md (config/vertical_routing.py + config/close_bank.py)
- **Resume file:** None — continue with 01-03-PLAN.md
