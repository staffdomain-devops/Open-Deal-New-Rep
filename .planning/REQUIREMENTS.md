# Requirements — Staff Domain Re-Engagement Pipeline

**Version:** v1 (2026-08-19)
**Source:** SD_Reengagement_LaneA_Build_Spec.md v1.0 + v1.1 amendment + research findings

---

## v1 Requirements

### Assembly — Stage 1

- [ ] **ASSM-01**: Pipeline fetches the company record associated with each contact from HubSpot
- [ ] **ASSM-02**: Pipeline fetches all contacts associated with that company (firstname, lastname, jobtitle, num_contacted_notes, notes_last_contacted) to identify colleagues for brief
- [ ] **ASSM-03**: Pipeline fetches all company-level deals (dealname, dealstage, createdate, closedate); filters junk deals (names starting with "(Test)", "(delete)", or containing "test" as standalone token)
- [ ] **ASSM-04**: Pipeline fetches notes for the contact and 1–2 key colleagues; filters bot-noise patterns (job-ad alerts, enrichment notifications); extracts live hiring signals into a separate brief field; tags sensitive content INTERNAL - NEVER REFERENCE
- [ ] **ASSM-05**: Pipeline resolves the handover name by fetching CALL and outbound EMAIL engagements for the contact; takes the more recent of the two by hs_timestamp; resolves owner ID to first name via owners API; records whether that owner isActive
- [ ] **ASSM-06**: Pipeline resolves the sending rep's first name from the contact's current hubspot_owner_id via the HubSpot owners API
- [ ] **ASSM-07**: Pipeline determines country from company record (AU/NZ/US/UK); applies per-geography copy rules and timezone resolution to brief; non-AU records receive country-neutral brief instructions
- [ ] **ASSM-08**: Pipeline builds a plain-text research brief per spec §3.7 field order for each contact that passes filters
- [ ] **ASSM-09**: A rate-limiter class enforces HubSpot API limits (100 req/10s general; 4 req/s for CRM Search endpoints) proactively, not just via retry-after headers

### Filtering — Stage 2

- [ ] **FILT-01**: Pipeline applies all 6 exclusion filters (E1: last-activity owner == current owner; E2: last-activity owner still active but not current owner; E3: activity in last 14 days; E4: duplicate contact records; E5: no engagement history; E6: unsubscribed/bounced/invalid email)
- [ ] **FILT-02**: Excluded contacts are written to an exclusion report (with filter code and reason) — not discarded; report is included in the Teams notification for JP review before generation proceeds

### Routing — Stage 3

- [ ] **ROUT-01**: Pipeline maps the contact's industry property to a case study name + email 3 and email 4 content URLs using the routing table from spec §4 (substring match, case-insensitive, first-hit wins; no-match fallback applies)

### Generation — Stage 4

- [ ] **GEN-01**: Pipeline submits one Anthropic Batch API request per contact using Claude Sonnet 5 (`claude-sonnet-5`); all contacts in a run are submitted as a single batch
- [ ] **GEN-02**: System prompt applied verbatim from spec §5.2 with 1-hour ephemeral cache TTL (`{"type": "ephemeral", "ttl": "1h"}`) — this corrects a spec bug where `{type: "ephemeral"}` defaults to 5-min TTL, which expires mid-batch
- [ ] **GEN-03**: Close bank option (1–5 from spec §5.3) assigned by code before generation using the rules: option 3 for >= 30 touches, option 4 for C-suite titles, option 5 for strong case-study match, otherwise rotate 1→2
- [ ] **GEN-04**: Each batch request uses `custom_id = contact_id` for O(1) result mapping after batch completes
- [ ] **GEN-05**: Batch ID is persisted as a GitHub Actions artifact before the polling loop begins (prevents data loss if the Actions job times out before batch completes)
- [ ] **GEN-06**: Pipeline polls batch status every 60 seconds until `processing_status == "ended"`; results are streamed and matched by `custom_id`; result types (succeeded/errored/canceled/expired) are each handled explicitly
- [ ] **GEN-07**: Generated output per contact is 8-key JSON (e1–e5 each with subject + body, call1 body, call2 body, pin body) per schema from spec §2
- [ ] **GEN-08**: Max tokens set to 3000 per spec v1.1 amendment (covers 5 emails + 2 call notes + 1 pin)

### Lint — Stage 5

- [ ] **LINT-01**: All 18 lint checks from spec §7.1–§7.2 applied to every contact's output (hard failures 1–12 from v1.0 + checks 13–18 from v1.1 amendment)
- [ ] **LINT-02**: Hard failure → regenerate once; if still failing → flag contact for JP review, skip write-back; never write invalid output to HubSpot
- [ ] **LINT-03**: Soft warnings (§7.2) flag contact for human review without blocking write-back
- [ ] **LINT-04**: Idempotency guard: before Stage 7 write-back, pipeline checks whether `email_1_subject` is already non-empty on the contact; skips write unless `--force-regenerate` flag is passed (prevents double-write from duplicate triggers)
- [ ] **LINT-05**: Pipeline accumulates `message.usage` (input tokens, output tokens, cache read/write tokens) per contact throughout the batch; total token counts and estimated dollar cost are calculated for the run summary

### Assemble Bodies — Stage 6

- [ ] **BODY-01**: Case study link placeholder appended to e2 body after generation: `[Insert {case study name} case study link here]` on a new line after sign-off
- [ ] **BODY-02**: Booking link placeholder appended to e5 body: `[Insert rep booking link here]` on a new line after sign-off
- [ ] **BODY-03**: Pre-send guard: pipeline rejects any assembled email body that contains a literal `[` character (catches un-substituted placeholders before write-back)

### Write-back — Stage 7

- [ ] **WB-01**: Preflight check verifies all 10 email contact properties (`email_1_subject` through `email_5_body`) exist in HubSpot and have `fieldType: textarea`; pipeline fails fast with an actionable error if any property is missing or has the wrong type (prevents silent paragraph-break stripping)
- [ ] **WB-02**: Pipeline writes 10 email contact properties per contact via HubSpot batch update API (100 contacts per batch, matched by `hs_object_id`)
- [ ] **WB-03**: Pipeline writes call task note for Call 1 (Day 7) to a contact property (default: `task_note_1`; exact property name confirmed before build)
- [ ] **WB-04**: Pipeline writes call task note for Call 2 (Day 17) to a contact property (default: `task_note_2`; exact property name confirmed before build)
- [ ] **WB-05**: Pipeline writes pinned contact note content to a contact property (default: `pin_note`; exact property name confirmed before build)
- [ ] **WB-06**: Contacts that fail in any pipeline stage after all retries are written to `failed_contacts.json` as a GitHub Actions artifact; pipeline also enrolls failed contacts in a designated HubSpot segment (segment ID configured via environment variable) for retry

### Review and Notifications

- [ ] **REV-01**: Exclusion report (all E1–E6 contacts with filter code and reason) posted to Teams via Power Automate webhook before generation begins; JP must review exclusions before the run proceeds
- [ ] **REV-02**: Post-generation review file (every 10th processed contact + all soft-warning contacts) posted to Teams for JP eyes-on sampling
- [ ] **REV-03**: Run cost summary (total input/output/cache tokens, estimated dollar cost) posted to Teams at end of run
- [ ] **REV-04**: Failed contacts list (contacts that errored in any stage, with stage and error) posted to Teams
- [ ] **REV-05**: All Teams notifications sent via Power Automate webhook URL (stored as GitHub Secret `TEAMS_WEBHOOK_URL`; legacy `webhook.office.com` URLs are not supported)

### Infrastructure

- [ ] **INFRA-01**: GitHub Actions `workflow_dispatch` trigger; HubSpot workflow passes contact IDs as a JSON array in the workflow inputs payload
- [ ] **INFRA-02**: Two-job GitHub Actions workflow: `prepare` job (Stages 1–3 + batch submission) and `complete` job (Batch API polling + Stages 5–7); `complete` depends on `prepare`
- [ ] **INFRA-03**: Batch ID written as GitHub Actions artifact by `prepare` job; `complete` job reads it to resume polling
- [ ] **INFRA-04**: GitHub Secrets: `HUBSPOT_TOKEN` (HubSpot Private App token), `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL`
- [ ] **INFRA-05**: Python 3.12 package structure: `pipeline/` subpackage with stage modules (`assemble.py`, `filter.py`, `route.py`, `generate.py`, `lint.py`, `assemble_bodies.py`, `write_back.py`); `run.py` as sole orchestrator entry point

---

## v2 Requirements

Features deferred to a later milestone — either after v1 ships or when Lane B spec arrives.

- Realtime API mode for local dev iteration (< 20 contacts without Batch API)
- Lane B implementation (pending Lane B Build Spec v2.1)
- HubSpot task engagement creation directly from pipeline (CALL type tasks with due dates) — v1 uses contact properties instead
- HubSpot note pinning via engagements API (`hs_pinned_engagement_id`) — v1 writes pin content to a contact property
- Cost regression detection across runs
- Routing coverage report (which verticals matched, which used the fallback)
- Lane config abstraction for multi-lane architecture

---

## Out of Scope

- HubSpot sequence creation — sequences are built and managed manually in HubSpot
- Automatic sequence enrollment — enrollment triggered by HubSpot workflow, not this pipeline
- Frontend or UI — pipeline is triggered programmatically via GitHub Actions
- Lane B — no spec yet; will be added when Lane B Build Spec v2.1 is available
- HubSpot engagement creation (CALL type tasks with due dates/assignments) — simplified to contact properties in v1

---

## Traceability

| REQ-ID | Phase | Status |
|--------|-------|--------|
| ASSM-01 | Phase 2 | Pending |
| ASSM-02 | Phase 2 | Pending |
| ASSM-03 | Phase 2 | Pending |
| ASSM-04 | Phase 2 | Pending |
| ASSM-05 | Phase 2 | Pending |
| ASSM-06 | Phase 2 | Pending |
| ASSM-07 | Phase 2 | Pending |
| ASSM-08 | Phase 2 | Pending |
| ASSM-09 | Phase 1 | Pending |
| FILT-01 | Phase 3 | Pending |
| FILT-02 | Phase 3 | Pending |
| ROUT-01 | Phase 3 | Pending |
| GEN-01 | Phase 4 | Pending |
| GEN-02 | Phase 4 | Pending |
| GEN-03 | Phase 4 | Pending |
| GEN-04 | Phase 4 | Pending |
| GEN-05 | Phase 4 | Pending |
| GEN-06 | Phase 4 | Pending |
| GEN-07 | Phase 4 | Pending |
| GEN-08 | Phase 4 | Pending |
| LINT-01 | Phase 5 | Pending |
| LINT-02 | Phase 5 | Pending |
| LINT-03 | Phase 5 | Pending |
| LINT-04 | Phase 5 | Pending |
| LINT-05 | Phase 5 | Pending |
| BODY-01 | Phase 5 | Pending |
| BODY-02 | Phase 5 | Pending |
| BODY-03 | Phase 5 | Pending |
| WB-01 | Phase 6 | Pending |
| WB-02 | Phase 6 | Pending |
| WB-03 | Phase 6 | Pending |
| WB-04 | Phase 6 | Pending |
| WB-05 | Phase 6 | Pending |
| WB-06 | Phase 6 | Pending |
| REV-01 | Phase 6 | Pending |
| REV-02 | Phase 6 | Pending |
| REV-03 | Phase 6 | Pending |
| REV-04 | Phase 6 | Pending |
| REV-05 | Phase 6 | Pending |
| INFRA-01 | Phase 7 | Pending |
| INFRA-02 | Phase 7 | Pending |
| INFRA-03 | Phase 7 | Pending |
| INFRA-04 | Phase 1 | Pending |
| INFRA-05 | Phase 1 | Pending |
