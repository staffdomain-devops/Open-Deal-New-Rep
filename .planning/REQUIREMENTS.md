# Requirements — Lane A Owner-Changed Re-Engagement Pipeline

## v1 Requirements

### Scaffolding

- [x] **SCAF-01**: `scripts/utils.py` copied from Inbound project unchanged (write_dlq, retry helpers, safe_truncate) — completed 01-01 (commit 7982692)
- [x] **SCAF-02**: `requirements.txt` lists five dependencies with minimum version constraints (`hubspot-api-client>=12.0.0`, `requests>=2.31.0`, `beautifulsoup4>=4.12.0`, `anthropic>=0.30.0`, `tenacity>=9.0.0`) — completed 01-01 (commit 096f2f2)
- [x] **SCAF-03**: `config/vertical_routing.py` implements 14-row vertical routing table (spec §4) — substring match, case-insensitive, first hit wins — completed 01-02 (commit fd5fb12)
- [x] **SCAF-04**: `config/close_bank.py` implements 5-option close bank with assignment logic (spec §5.3): option 3 for ≥30 touches, option 4 for C-suite, option 5 for strong case-study match, else rotate 1→2→1→2 — completed 01-02 (commit 4db1144)
- [x] **SCAF-05**: `config/system_prompt.py` holds the verbatim system prompt from spec §5.2 (v1.0 + v1.1 amendments) and the v1.1 OUTPUT block appended instruction — completed 01-03 (commit 310628c)

### Data Fetch & Record Assembly (Stage 1)

- [x] **FETCH-01**: `scripts/fetch_list.py` accepts `INPUT_LIST_ID` env var; fetches all contact IDs on the HubSpot list via the Lists API — completed 02-01 (commit 1c85ba3)
- [x] **FETCH-02**: For each contact, fetch contact properties: `firstname`, `lastname`, `jobtitle`, `company`, `industry`, `hubspot_owner_id`, `email`, `hs_email_optout`, `num_contacted_notes`, `notes_last_contacted`, `country`, `phone` — completed 02-02 (commit 01dae1b)
- [x] **FETCH-03**: Fetch associated company; fetch company properties: `name`, `industry`, `country` — completed 02-02 (commit 01dae1b)
- [x] **FETCH-04**: Fetch all contacts associated with that company; for each capture `firstname`, `lastname`, `jobtitle`, `num_contacted_notes`, `notes_last_contacted` — completed 02-02 (commit 01dae1b)
- [x] **FETCH-05**: Fetch all deals associated with the company; capture `dealname`, `dealstage`, `createdate`, `closedate`; filter junk deals (`(Test)`, `(delete)`, standalone `test`) — completed 02-02 (commit 01dae1b)
- [x] **FETCH-06**: Fetch notes on the contact and 1–2 key colleagues; apply bot-noise filter (discard notes whose body starts with or contains within first 80 chars: job-ad alert prefixes from spec §3.3); parse job-ad notes separately into live hiring signals — completed 02-02 (commit 01dae1b)
- [x] **FETCH-07**: Resolve handover name — fetch all CALL engagements (most recent by `hs_timestamp`) and all outbound EMAIL engagements (`hs_email_direction == EMAIL`) for the contact; take later of the two; resolve owner ID to name + `isActive` via owners API — completed 02-02 (commit 01dae1b)
- [ ] **FETCH-08**: Geo resolution — resolve company country to AU / NZ / US / UK via resolution ladder (spec §3.6 / Lane B v2.1 §3.6); flag unresolved as data-error hold
- [ ] **FETCH-09**: Contact departure check — scan retained notes for "has left" / "no longer with" / "moved on from" against the contact's own name; flag hits for JP review (spec v1.1 checklist item 17)
- [ ] **FETCH-10**: Mark whether the recipient is the only contacted person on the company (brief must say so if true)
- [ ] **FETCH-11**: Write per-contact assembled data to `$RUNNER_TEMP/contact_{id}.json`

### Exclusion Filters (Stage 2)

- [ ] **EXCL-01**: E1 — skip if last-activity owner ID == current `hubspot_owner_id`; add to exclusion report with reason
- [ ] **EXCL-02**: E2 — hold if last-activity owner is still `isActive` but is not current owner; add to exclusion report
- [ ] **EXCL-03**: E3 — skip if any engagement in the last 14 days
- [ ] **EXCL-04**: E4 — detect duplicate contact records (same email + same company ID, different record IDs); report for merge
- [ ] **EXCL-05**: E5 — skip if zero CALL engagements and zero outbound EMAIL engagements
- [ ] **EXCL-06**: E6 — skip if contact email is unsubscribed (`hs_email_optout`), bounced, or invalid
- [ ] **EXCL-07**: Write `$RUNNER_TEMP/exclusion_report.json` with all excluded contacts, their IDs, and reason codes; do NOT silently discard

### Routing & Brief Assembly (Stage 3)

- [ ] **ROUTE-01**: Apply vertical routing table; record matched row (case study name, email 3 URL, email 4 URL); fallback to default row if no match or industry empty
- [ ] **ROUTE-02**: Assign close bank option per assignment rules; code rotates 1→2→1→2 as default
- [ ] **ROUTE-03**: Assemble per-contact research brief in exact field-order format from spec §3.7; mark sensitive items `INTERNAL - NEVER REFERENCE`; mark job-ad signals `LIVE HIRING SIGNALS (public job ads)`
- [ ] **ROUTE-04**: No-deal branch: if company has zero non-junk deals, brief must state "No previous deal on record. Do not invent one."
- [ ] **ROUTE-05**: Colleague rule: include first name + job title for 1–2 most-contacted colleagues who have `num_contacted_notes >= 1`; if recipient is only contacted person, brief says so explicitly

### Generation (Stage 4)

- [ ] **GEN-01**: `scripts/generate_campaign.py` reads per-contact JSON and calls `claude-sonnet-5`
- [ ] **GEN-02**: System prompt sent with `cache_control: {type: "ephemeral"}`; user message is the assembled brief
- [ ] **GEN-03**: `max_tokens=3000`; one call per contact; generates all 8 deliverables in one response
- [ ] **GEN-04**: Realtime API for iteration/pilot; Batch API (`anthropic.batches`) wired for full-list runs (toggle via `INPUT_USE_BATCH_API` env var)
- [ ] **GEN-05**: Output parsed against 8-key schema: `e1`–`e5` (each `subject` + `body`) + `call1` + `call2` + `pin` (each `body` only)
- [ ] **GEN-06**: On `stop_reason == "max_tokens"`, raise clear error; on JSON parse failure, save raw response before raising
- [ ] **GEN-07**: Write result to `$RUNNER_TEMP/generated_{id}.json`

### Lint Engine (Stage 5)

- [ ] **LINT-01**: Hard checks 1–12 from spec v1.0 §7.1 (JSON schema, em dash, banned words, offshore vocabulary, subject rules, URL whitelist, sign-off, word count, close bank match, name invention, no-agenda phrase, INTERNAL leak)
- [ ] **LINT-02**: Hard checks 13–18 from spec v1.1 §7 (8-key schema + word caps, required labels, Lane A voicemail framing, sensitive-material direction, timezone warning, sequence map string)
- [ ] **LINT-03**: Soft warnings from spec v1.0 §7.2 (tic-capped phrases, 4+-word repeated phrases, email 3 missing question mark, inline URL position, cross-contact duplicate subjects)
- [ ] **LINT-04**: On hard fail: regenerate once; if second attempt also fails, flag contact for manual review; do not write to HubSpot
- [ ] **LINT-05**: Human review sample: dump every 10th output + all soft-warning outputs to `$RUNNER_TEMP/review_sample.json` for JP

### Body Assembly (Stage 6)

- [ ] **ASSEM-01**: Append to E2 body (after sign-off): `[Insert {case study name} case study link here]`
- [ ] **ASSEM-02**: Append to E5 body (after sign-off): `[Insert rep booking link here]`
- [ ] **ASSEM-03**: E1, E3, E4 get no appended content (inline URLs already in E3/E4 body from generation)

### HubSpot Write-back (Stage 7)

- [ ] **WRITE-01**: Batch update 12 contact properties per contact: `email_1_subject`, `email_1_body` … `email_5_subject`, `email_5_body` (10 email props) + `task_note_1` (Call 1 briefing) + `task_note_2` (Call 2 briefing) — all **multi-line text** type; 100 records per batch; match by `hs_object_id`
- [ ] **WRITE-02**: Pre-write: verify all 12 properties exist as multi-line text type on first run (abort if any is single-line text)
- [ ] **WRITE-03**: Pre-send bracket guard: check no `[` appears in any of the 10 email properties on enrolled contacts; fail hard if found (task_note properties are internal-only and exempt from this check)
- [ ] **WRITE-04**: Create note engagement with pin body; pin to contact record (verify API support at pilot; fallback: log manual pin list)
- [ ] **WRITE-05**: Paragraph separator: real `\n\n` in all body properties; verify rendering in sequence editor on 2–3 records before full run

### Error Handling

- [ ] **ERR-01**: All scripts implement exponential backoff retry (up to 6 attempts, 60s max) on 429 and 5xx — reuse `utils.py` from Inbound
- [ ] **ERR-02**: Each script writes DLQ sentinel at startup; updates with error details on failure
- [ ] **ERR-03**: GitHub Actions uploads `failed_contacts.json` and `exclusion_report.json` as artifacts on failure
- [ ] **ERR-04**: GitHub Actions POSTs Teams webhook on failure with contact info, failed step, error excerpt, run log link

### CI/CD

- [ ] **CI-01**: `.github/workflows/campaign.yml` orchestrates: fetch list → for each passing contact: assemble → exclude → route → generate → lint → assemble bodies → write-back
- [ ] **CI-02**: `workflow_dispatch` inputs: `list_id` (required), `pilot_mode` (boolean, default true — caps at 20 contacts)
- [ ] **CI-03**: Pilot mode enforced: if `pilot_mode=true` and contact count > 20, process only first 20 and log a warning
- [ ] **CI-04**: Upload `exclusion_report.json` and `review_sample.json` as artifacts on every run (not just failure)
- [ ] **CI-05**: Upload `campaign_output.json` (full batch results) as artifact on success
- [ ] **CI-06**: Secrets: `HUBSPOT_API_KEY`, `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL`

## v2 Requirements (Deferred)

- Batch API polling and result reconciliation for large runs (>500 contacts)
- Cross-lane deduplication (contact enrolled in Lane B should not get Lane A too)
- Automated test fixtures with mock HubSpot / Anthropic responses
- Reply-kill workflow: unenrol sequence + close open Lane A call tasks on inbound reply (HubSpot workflow or daily sweep — verify at pilot)
- Dashboard: JP-facing run summary (contacts processed, excluded, generated, written)

## Out of Scope

- Sending emails — HubSpot sequences handle send
- Lane B pipeline — separate project
- Breeze prompt generation — this pipeline writes finished emails
- ZoomInfo enrichment — data already in HubSpot
- Per-contact trigger mode — batch only

## Traceability

| REQ-ID | Phase |
|--------|-------|
| SCAF-01–05 | Phase 1 |
| FETCH-01–11 | Phase 2 |
| EXCL-01–07 | Phase 3 |
| ROUTE-01–05 | Phase 3 |
| GEN-01–07 | Phase 4 |
| LINT-01–05 | Phase 5 |
| ASSEM-01–03 | Phase 5 |
| WRITE-01–05 | Phase 6 |
| ERR-01–04 | Phase 2–6 (each script) + Phase 7 |
| CI-01–06 | Phase 7 |
