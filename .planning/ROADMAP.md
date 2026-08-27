# Roadmap — Lane A Owner-Changed Re-Engagement Pipeline

**7 phases** | **All v1 requirements covered** ✓

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|-----------------|
| 1 | Scaffolding | Project structure, shared utilities, routing/close/prompt configs | SCAF-01–05 | 5 |
| 2 | Record Assembly | 3/3 | Complete   | 2026-08-21 |
| 3 | Exclusion & Routing | Exclusion filters E1–E6, vertical routing, close bank, brief assembly | EXCL-01–07, ROUTE-01–05 | 5 |
| 4 | Generation | Claude API call with cached system prompt, 8-deliverable output | GEN-01–07 | 4 |
| 5 | Lint & Assembly | 18-check lint engine, soft warnings, review sample, link placeholder append | LINT-01–05, ASSEM-01–03 | 5 |
| 6 | Write-back | 2/2 | Complete | 2026-08-27 |
| 7 | CI/CD | GitHub Actions workflow, pilot mode, artifacts, failure handling | CI-01–06, ERR-03–04 | 5 |

---

### Phase 1: Scaffolding

**Goal:** Establish the project structure, copy shared utilities from Inbound, and encode the three static configs (routing table, close bank, system prompt) that every subsequent phase depends on.
**Mode:** mvp

**Requirements:** SCAF-01–05

**Plans:** 3 plans

Plans:
- [x] 01-01-PLAN.md — Copy utils.py from Inbound + create requirements.txt (SCAF-01, SCAF-02)
- [x] 01-02-PLAN.md — Implement vertical routing table + close bank (SCAF-03, SCAF-04)
- [x] 01-03-PLAN.md — Encode verbatim system prompt with v1.1 edits (SCAF-05)

**Success Criteria:**
1. `scripts/utils.py` copied from Inbound unchanged with all retry/DLQ helpers present
2. `requirements.txt` lists all five dependencies with minimum versions
3. `config/vertical_routing.py` returns correct (case_study, email3_url, email4_url) tuple for at least 6 industry test strings across different table rows
4. `config/close_bank.py` returns correct option for all four assignment rule branches (C-suite → 4, heavy contact → 3, strong match → 5, default → 1/2 rotation)
5. `config/system_prompt.py` contains verbatim system prompt with v1.1 OUTPUT block appended; no paraphrasing

---

### Phase 2: Record Assembly

**Goal:** `scripts/fetch_record.py` fetches and assembles the full per-contact research package: company record, all company contacts, company deals (filtered), notes (bot-noise filtered + signals parsed), handover name (via engagement API), geo resolution, and contact departure check. Writes `contact_{id}.json`.
**Mode:** mvp

**Requirements:** FETCH-01–11

**Plans:** 3/3 plans complete

Plans:

**Wave 1** *(02-01 and 02-02 run in parallel — different files)*
- [x] 02-01-PLAN.md — Implement fetch_list.py: paginated HubSpot list fetch, writes contact_ids.json (FETCH-01)
- [x] 02-02-PLAN.md — Implement fetch_record.py skeleton + six core fetch functions: contact, company, company contacts, deals, notes/signals/sensitive, handover (FETCH-02–07)

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 02-03-PLAN.md — Add geo resolution, departure check, only-contact flag, and final D-13 JSON assembly to fetch_record.py (FETCH-08–11)

**Success Criteria:**
1. Running against a real contact writes `contact_{id}.json` containing: `contact_props`, `company_props`, `all_company_contacts` (with `num_contacted_notes`), `deals` (junk filtered), `story_notes` (bot-noise removed), `live_hiring_signals`, `handover` (first name, last contact date, method, `is_active`), `geo` (AU/NZ/US/UK or error), `is_only_contact` (bool), `sensitive_items` (list)
2. Bot-noise filter correctly drops job-ad alert notes; live hiring signals list is populated from those same notes
3. Handover resolution correctly picks the later of most-recent CALL vs most-recent outbound EMAIL; falls back gracefully if neither exists
4. Geo resolution returns AU for an Australian phone/country, and flags an unresolved record rather than guessing
5. Contact departure check flags a record whose notes contain "has left" against the contact's name

---

### Phase 3: Exclusion & Routing

**Goal:** `scripts/exclude_and_route.py` reads each `contact_{id}.json`, applies the six exclusion filters, routes passing contacts through the vertical table and close bank assignment, and assembles the final §3.7 brief. Writes `brief_{id}.json` for passing contacts and `exclusion_report.json` for all excluded/held contacts.

**Requirements:** EXCL-01–07, ROUTE-01–05

**Plans:** 2 plans, 2 waves

**Wave 1**
- [x] 03-01-PLAN.md — Amend fetch_record.py (owner_id in handover, company_id in schema, hs_email_bounce in CONTACT_PROPS) + create exclude_and_route.py skeleton + E1–E6 exclusion filters + GEO_UNRESOLVED hold + exclusion_report.json (EXCL-01–07)

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 03-02-PLAN.md — Add route_contact(), build_brief() (exact §3.7 format), wire into main(), write brief_{id}.json per passing contact (ROUTE-01–05)

**Success Criteria:**
1. All six exclusion filters (E1–E6) implemented; each excluded contact lands in `exclusion_report.json` with its ID, filter code, and a human-readable reason
2. `exclusion_report.json` written for every run (even if empty); never silent discard
3. Brief assembled in exact §3.7 field order; no-deal branch produces the correct brief text; only-contact branch omits colleague lines
4. Vertical routing returns correct case study name and URLs for at least 4 industry test inputs; falls back to default row on empty industry
5. Close bank assignment respects all four override rules; `brief_{id}.json` carries the assigned close option number

---

### Phase 4: Generation

**Goal:** `scripts/generate_campaign.py` reads `brief_{id}.json`, builds the user message, calls `claude-sonnet-5` with the cached system prompt, parses the 8-key response, and writes `generated_{id}.json`. Supports realtime API for iteration and Batch API for full runs.
**Mode:** mvp

**Requirements:** GEN-01–07

**Plans:** 2 plans

Plans:

**Wave 1**
- [x] 04-01-PLAN.md — Amend exclude_and_route.py to write passing_ids.json + create generate_campaign.py skeleton with realtime API path (GEN-01, GEN-02, GEN-03, GEN-05, GEN-06, GEN-07)

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 04-02-PLAN.md — Add Batch API path: submit_batch(), poll_batch(), process_batch_results(), INPUT_USE_BATCH_API toggle (GEN-04)

**Success Criteria:**
1. System prompt sent with `cache_control: {type: "ephemeral"}`; model is `claude-sonnet-5`; `max_tokens=3000`
2. Response parsed against 8-key schema (`e1`–`e5` each with `subject`/`body`; `call1`, `call2`, `pin` each with `body`)
3. `stop_reason == "max_tokens"` raises a named error; JSON parse failure saves raw response to RUNNER_TEMP before raising
4. Batch API mode wired and toggled via `INPUT_USE_BATCH_API=true`; realtime is the default

---

### Phase 5: Lint & Body Assembly

**Goal:** `scripts/lint.py` applies all 18 hard checks and 5 soft warnings from the spec. Hard failures trigger one regeneration attempt then flag. Soft warnings accumulate into `review_sample.json`. Every 10th output also dumped to the review file. `scripts/assemble_bodies.py` appends link placeholders to E2 and E5.
**Mode:** mvp

**Requirements:** LINT-01–05, ASSEM-01–03

**Plans:** 2 plans

**Wave 1**
- [x] 05-01-PLAN.md — Create lint.py: all 18 hard check functions (H01–H18) + 5 soft warning functions (W01–W05) + run_lint() + _write_to_review_sample() (LINT-01, LINT-02, LINT-03, LINT-05)

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 05-02-PLAN.md — Add _regenerate_contact() + main() to lint.py + create assemble_bodies.py (LINT-04, ASSEM-01, ASSEM-02, ASSEM-03)

**Success Criteria:**
1. All 12 hard checks from v1.0 implemented; test vector with a known em-dash failure and a known banned-word failure each triggers regeneration
2. All 6 hard checks from v1.1 implemented; a call note missing the HISTORY label fails check 14; a call1 IF VOICEMAIL line with "catch up" fails check 15
3. Soft warnings accumulate; a soft-warning contact lands in `review_sample.json`; every 10th passing contact also lands there
4. E2 body ends with `[Insert {case study name} case study link here]` on its own line after the sign-off; E5 ends with `[Insert rep booking link here]`
5. E1 body has zero URLs; E3/E4 each have exactly one URL (the inline URL from the routing table), present mid-body with a lead-in sentence

---

### Phase 6: Write-back

**Goal:** `scripts/write_hubspot.py` writes 12 contact properties (10 email + `task_note_1` + `task_note_2`) and creates + pins 1 contact note per contact. Call tasks are created manually by the rep — the pipeline only provides the briefing text via the two task_note properties.
**Mode:** mvp

**Requirements:** WRITE-01–05, ERR-01–02

**Plans:** 2/2 plans complete

Plans:

**Wave 1**
- [x] 06-01-PLAN.md — Create write_hubspot.py: property schema check, bracket guard, batch property write (WRITE-01, WRITE-02, WRITE-03, WRITE-05, ERR-01, ERR-02)

**Wave 2** *(blocked on Wave 1 completion)*
- [x] 06-02-PLAN.md — Add note creation + pin with manual fallback to write_hubspot.py (WRITE-04)

**Success Criteria:**
1. On first run, script checks all 12 properties exist as multi-line text type; aborts with clear error if any is wrong type
2. Bracket guard scans all 10 email property values before write; raises error if `[` found; task_note properties are internal-only and exempt
3. `task_note_1` and `task_note_2` written with the generated call briefing text (plain text, preserving `\n` line structure from the generated output)
4. Note created and pinned (or logged to manual-pin list if API doesn't support pinning); pin body matches generated output
5. DLQ sentinel written at startup; updated with error + retry_count on any failure

---

### Phase 7: CI/CD

**Goal:** `.github/workflows/campaign.yml` orchestrates the full pipeline list-driven, with pilot mode cap, artifact uploads for exclusion report + review sample + full output, and Teams notification on failure.
**Mode:** mvp

**Requirements:** CI-01–06, ERR-03–04

**Success Criteria:**
1. `workflow_dispatch` inputs: `list_id` (required string), `pilot_mode` (boolean, default `true`)
2. Pilot mode: if `pilot_mode=true` and contacts > 20, workflow processes only first 20 and prints a warning line
3. `exclusion_report.json` uploaded as artifact on every run (success and failure); `review_sample.json` uploaded on every run
4. `campaign_output.json` uploaded as artifact on success with 7-day retention
5. On failure: `failed_contacts.json` uploaded AND Teams webhook POSTed with list_id, failed step, error excerpt, and run URL
