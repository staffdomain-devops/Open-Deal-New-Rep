# Roadmap — Lane A Owner-Changed Re-Engagement Pipeline

**7 phases** | **All v1 requirements covered** ✓

| # | Phase | Goal | Requirements | Success Criteria |
|---|-------|------|--------------|-----------------|
| 1 | Scaffolding | Project structure, shared utilities, routing/close/prompt configs | SCAF-01–05 | 5 |
| 2 | Record Assembly | Full Stage 1 data fetch per contact: company, contacts, deals, notes, handover, geo | FETCH-01–11 | 5 |
| 3 | Exclusion & Routing | Exclusion filters E1–E6, vertical routing, close bank, brief assembly | EXCL-01–07, ROUTE-01–05 | 5 |
| 4 | Generation | Claude API call with cached system prompt, 8-deliverable output | GEN-01–07 | 4 |
| 5 | Lint & Assembly | 18-check lint engine, soft warnings, review sample, link placeholder append | LINT-01–05, ASSEM-01–03 | 5 |
| 6 | Write-back | 10 email properties, 2 call tasks, 1 pinned note, sender binding, bracket guard | WRITE-01–07, ERR-01–02 | 6 |
| 7 | CI/CD | GitHub Actions workflow, pilot mode, artifacts, failure handling | CI-01–06, ERR-03–04 | 5 |

---

### Phase 1: Scaffolding

**Goal:** Establish the project structure, copy shared utilities from Inbound, and encode the three static configs (routing table, close bank, system prompt) that every subsequent phase depends on.
**Mode:** mvp

**Requirements:** SCAF-01–05

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

**Success Criteria:**
1. Running against a real contact writes `contact_{id}.json` containing: `contact_props`, `company_props`, `all_company_contacts` (with `num_contacted_notes`), `deals` (junk filtered), `story_notes` (bot-noise removed), `live_hiring_signals`, `handover` (first name, last contact date, method, `is_active`), `geo` (AU/NZ/US/UK or error), `is_only_contact` (bool), `sensitive_items` (list)
2. Bot-noise filter correctly drops job-ad alert notes; live hiring signals list is populated from those same notes
3. Handover resolution correctly picks the later of most-recent CALL vs most-recent outbound EMAIL; falls back gracefully if neither exists
4. Geo resolution returns AU for an Australian phone/country, and flags an unresolved record rather than guessing
5. Contact departure check flags a record whose notes contain "has left" against the contact's name

---

### Phase 3: Exclusion & Routing

**Goal:** `scripts/exclude_and_route.py` reads each `contact_{id}.json`, applies the six exclusion filters, routes passing contacts through the vertical table and close bank assignment, and assembles the final §3.7 brief. Writes `brief_{id}.json` for passing contacts and `exclusion_report.json` for all excluded/held contacts.
**Mode:** mvp

**Requirements:** EXCL-01–07, ROUTE-01–05

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

**Success Criteria:**
1. All 12 hard checks from v1.0 implemented; test vector with a known em-dash failure and a known banned-word failure each triggers regeneration
2. All 6 hard checks from v1.1 implemented; a call note missing the HISTORY label fails check 14; a call1 IF VOICEMAIL line with "catch up" fails check 15
3. Soft warnings accumulate; a soft-warning contact lands in `review_sample.json`; every 10th passing contact also lands there
4. E2 body ends with `[Insert {case study name} case study link here]` on its own line after the sign-off; E5 ends with `[Insert rep booking link here]`
5. E1 body has zero URLs; E3/E4 each have exactly one URL (the inline URL from the routing table), present mid-body with a lead-in sentence

---

### Phase 6: Write-back

**Goal:** `scripts/write_hubspot.py` writes 10 email properties, creates 2 call tasks, creates and pins 1 contact note per contact. Includes property-type pre-check, sender binding, and bracket guard.
**Mode:** mvp

**Requirements:** WRITE-01–07, ERR-01–02

**Success Criteria:**
1. On first run, script checks all 10 properties exist as multi-line text type; aborts with clear error if any is wrong type
2. Bracket guard scans all 10 property values before write; raises error if `[` found (means assemble_bodies step failed or a placeholder leaked)
3. Two CALL tasks created per contact with correct subjects (`touch 3 of 7` / `touch 6 of 7`), assigned to current `hubspot_owner_id`, with recipient-local due times
4. Note created and pinned (or logged to manual-pin list if API doesn't support pinning); pin body matches generated output
5. Paragraph breaks (`\n\n`) verified rendering in HubSpot sequence editor on at least 2 test records before full run (checklist item, not automated)
6. DLQ sentinel written at startup; updated with error + retry_count on any failure

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
