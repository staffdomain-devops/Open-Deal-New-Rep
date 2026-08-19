# Staff Domain Re-Engagement Pipeline

## What This Is

A Python pipeline running on GitHub Actions that generates personalized 7-touch re-engagement sequences for Staff Domain's past prospects. For each contact, it assembles a research brief from HubSpot data, applies exclusion logic, calls Claude Sonnet 5 to produce 8 deliverables (5 emails + 2 call task notes + 1 pinned contact note), lints the output, and writes everything back to HubSpot. Triggered by a HubSpot workflow via the GitHub Actions `workflow_dispatch` API, passing contact IDs in the payload.

## Core Value

Every email reads like someone went through the file — personalization changes the *reason* for each touch, not just the name.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] GitHub Actions workflow triggered by HubSpot via `workflow_dispatch` with contact IDs in payload
- [ ] **Stage 1 — Assemble**: fetch company, all associated contacts (with touch counts), deals, notes (bot-filtered), engagements; resolve handover name via engagements API (calls + outbound emails); resolve rep first name from HubSpot owners API; apply geography check; build plain-text research brief per spec §3
- [ ] **Stage 2 — Filter**: apply 6 exclusion filters (E1–E6); excluded contacts go to report (not discarded)
- [ ] **Stage 3 — Route**: map contact industry to case study name + email 3/4 URLs via routing table (§4)
- [ ] **Stage 4 — Generate**: one Claude Sonnet 5 call per contact; cached system prompt (ephemeral); max tokens 3000; produces 8-key JSON (e1–e5, call1, call2, pin); Batch API for full runs
- [ ] **Stage 5 — Lint**: 18 checks (hard failures regenerate once, then flag; soft warnings flag only); review file for every 10th contact + all soft warnings
- [ ] **Stage 6 — Assemble**: append case study placeholder to e2, booking link placeholder to e5
- [ ] **Stage 7 — Write back**: 10 email properties (multi-line text), 2 call task engagements (type CALL, assigned to contact owner, correct due dates), 1 pinned note; batch by `hs_object_id`
- [ ] Teams webhook notification for review file (exclusions report + soft-warning contacts)
- [ ] Shared core architecture: lane-specific configs as separate modules (Lane A now, Lane B when spec arrives)
- [ ] Dry-run mode: generate and lint without writing to HubSpot
- [ ] Pre-send guard: reject any email property containing a literal `[` placeholder bracket

### Out of Scope

- Lane B implementation — spec not yet written; shared core will accommodate it
- HubSpot sequence creation — sequences are built manually in HubSpot by the team
- Frontend / UI — pipeline is triggered programmatically
- Automatic sequence enrolment — enrolment handled by HubSpot workflow

## Context

- **Spec**: `SD_Reengagement_LaneA_Build_Spec.md` (v1.0, 17 Aug 2026) + `New SDR - Deal old deal outreach.md` (v1.1 amendment, 18 Aug 2026) define exact pipeline logic, voice rules, system prompt, close bank, lint checks, and routing table. These are the single source of truth — copy rule changes land in the spec first.
- **Sample run**: `Sample run.txt` — 5-contact pilot (3 genuine Lane A passes, 2 adapted non-handover). Pipeline logic and voice verified by JP.
- **HubSpot list**: 53672 (422 contacts at time of spec)
- **Model**: `claude-sonnet-5` via Anthropic API (Python SDK). System prompt cached with `cache_control: ephemeral`. Batch API for full runs (~$2–3 at 500 contacts).
- **Auth**: HubSpot Private App token + Anthropic API key stored as GitHub Secrets.
- **Lane A premise**: contact owner has changed since last contact; previous rep has left the business. Emails written as warm handover from new account manager.

## Constraints

- **Tech**: Python, `anthropic` SDK, HubSpot API v3 (private app token), GitHub Actions
- **Trigger**: `workflow_dispatch` called by HubSpot workflow; contact IDs in JSON payload
- **System prompt**: locked word-for-word in spec §5.2 — do not paraphrase or "improve"
- **Close bank**: 5 locked closes (§5.3) — model must use exact wording, code assigns per contact
- **URL whitelist**: exact list in §4 — linter rejects any other URL; slugs containing `offshore`, `outsourcing`, `bpo` banned even if on-site
- **HubSpot write-back**: body properties must be MULTI-LINE TEXT type; verify before first write
- **Rep name substitution**: resolved at generation time from HubSpot owners API (`hubspot_owner_id` on the contact); substituted into generated bodies before write-back

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Rep first name substituted at generation time | Contact body properties store finished copy; HubSpot sequence just pulls the token — avoids double sign-off | — Pending |
| Batch API for full runs, realtime for dev/iteration | ~50% cost reduction at scale; dev loop doesn't need the savings | — Pending |
| Shared core, lane configs as separate modules | Lane B spec incoming; don't duplicate pipeline infrastructure | — Pending |
| Review notifications via Teams webhook | Fits existing team communication; JP reviews exclusions + samples before send | — Pending |
| Note-pinning via engagements API (verify at pilot) | Pinning API support unconfirmed; fallback is manual pin pass | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-08-19 after initialization*
