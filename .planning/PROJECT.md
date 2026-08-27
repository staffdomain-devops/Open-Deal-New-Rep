# Lane A Owner-Changed Re-Engagement Pipeline

## What This Is

A batch generation pipeline that produces 8 personalised deliverables per past prospect where the HubSpot contact owner has changed. The pipeline runs against a HubSpot list, assembles a research brief per contact from company data, deal history, engagement notes, and resolved handover name, then calls Claude Sonnet 5 once per contact to generate 5 re-engagement emails + 2 call-task briefings + 1 pinned contact note. All output is written back to HubSpot.

## Core Value

Every new account owner inherits a warm relationship they have never personally touched. The pipeline turns the file into a credible, personalised handover sequence — so the rep sounds like they've read the notes, because the generation input includes the notes.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Accept a HubSpot list ID as input; iterate all contacts on that list
- [ ] For each contact: fetch company, all company contacts, all company deals, notes (with bot-noise filter), handover name via engagement API
- [ ] Apply exclusion filters E1–E6; write exclusion report for JP review
- [ ] Route contact to vertical via industry lookup table; assign close bank option
- [ ] Assemble per-contact research brief (spec §3.7 format)
- [ ] Generate 8 deliverables in one Sonnet call: 5 emails + 2 call notes + 1 pin note
- [ ] Lint all 18 checks; regenerate once on hard fail; write soft-warning review file
- [ ] Assemble final email bodies (append link placeholders to E2/E5)
- [ ] Write 12 contact properties to HubSpot: 10 email properties (multi-line text) + `task_note_1` + `task_note_2` (call briefing text for manual task creation by rep)
- [ ] Create + pin 1 contact note per contact
- [ ] Pre-send bracket guard: no literal `[` placeholder may reach an email property
- [ ] GitHub Actions workflow: list-driven, pilot mode (20-record cap) + full run
- [ ] Exclusion report and human-review sample dumped as workflow artifacts
- [ ] Failure handling: DLQ + Teams/Slack notification

### Out of Scope

- Per-contact trigger mode — this pipeline is batch, list-driven
- Sending emails directly — HubSpot sequences handle send
- ZoomInfo enrichment — data already in HubSpot before the pipeline runs
- Lane B (different owner scenario) — separate pipeline
- Breeze prompt generation — this pipeline writes finished emails

## Context

Architecture mirrors the Inbound pipeline (`C:\Users\irahfo\Outreach\Inbound\`) in structure (Python scripts, GitHub Actions, `utils.py`, `requirements.txt`, `RUNNER_TEMP` JSON hand-off). Key differences from Inbound:

| Dimension | Inbound | Lane A |
|---|---|---|
| Trigger | Per-contact via Make.com | Batch via list ID |
| Data assembly | Simple fetch + enrich | 7-step brief assembly (company contacts, deals, notes filter, handover resolution, exclusion, routing, brief format) |
| Deliverables | 1 email | 8 (5 emails + 2 call notes + 1 pin) |
| HubSpot write | 2 properties + 1 note | 12 properties (`email_1_subject`…`email_5_body` + `task_note_1` + `task_note_2`) + 1 pinned note |
| Lint | Basic (em-dash, JSON) | 18 checks (12 hard v1.0 + 6 hard v1.1) |
| Model | `claude-sonnet-4-6` | `claude-sonnet-5` |
| System prompt | Short inline | Long verbatim (cached, `cache_control: ephemeral`) |
| Max tokens | 2048 | 3000 |
| Batch API | No | Yes (for full runs) |
| Routing | None | 14-row vertical table → case study + URLs |
| Close bank | None | 5 options, code-assigned |

Reuse `utils.py` from Inbound unchanged — same retry/DLQ patterns apply.

## Constraints

- **Tech Stack**: Python 3.12, GitHub Actions (ubuntu-latest), HubSpot private app token, Anthropic Claude API
- **Dependencies**: `hubspot-api-client>=12.0.0`, `requests>=2.31.0`, `beautifulsoup4>=4.12.0`, `anthropic>=0.30.0`, `tenacity>=9.0.0`
- **HubSpot Properties**: 10 multi-line text properties must exist before first run (`email_1_subject` … `email_5_body`). Verify type before write.
- **Secrets**: `HUBSPOT_API_KEY`, `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL`
- **Spec documents**: `SD_Reengagement_LaneA_Build_Spec.md` (v1.0) + `New SDR - Deal old deal outreach.md` (v1.1 amendment) — both in repo root
- **Model**: `claude-sonnet-5` — not `claude-sonnet-4-6`

## Key Decisions

| Decision | Rationale | Outcome |
|---|---|---|
| Batch list-driven, not per-contact | Volume requires bulk run; exclusion report must be reviewed by JP before send | — Pending |
| Reuse utils.py from Inbound | Same retry/DLQ/backoff patterns; no divergence needed | — Pending |
| One Claude call per contact, all 8 deliverables | Model needs full arc to avoid repetition; call notes reference email content | — Pending |
| Batch API for full runs | ~500 contacts; 50% cost reduction; switch to realtime for iteration | — Pending |
| Cached system prompt | Long verbatim prompt; 10% price for all contacts after first | — Pending |
| HubSpot write-back uses `hs_object_id` match | Batch update API, 100 per batch; consistent with spec §8 | — Pending |
| Call tasks created manually by rep; pipeline writes briefing to `task_note_1`/`task_note_2` | Simpler pipeline; rep controls task timing | Confirmed 2026-08-21 |
| Note pinning via engagements API | Verify support at pilot; manual fallback if API doesn't support pinning | — Pending |

---
*Last updated: 2026-08-27 — Phase 6 complete (write_hubspot.py: 12-property batch write, bracket guard, schema check, note creation + pin with manual fallback, DLQ). Phase 7 (CI/CD) is next.*

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted
