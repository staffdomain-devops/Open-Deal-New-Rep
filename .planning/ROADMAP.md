# Roadmap: Staff Domain Re-Engagement Pipeline

## Overview

A 7-phase build that follows the pipeline's own stage order. Phases 1-3 establish the data foundation and deterministic stages (assemble, filter, route) before any LLM work begins. Phase 4 introduces Anthropic Batch API generation. Phase 5 adds lint and body assembly to validate and finalize output. Phase 6 wires in HubSpot write-back and all Teams notifications. Phase 7 wraps everything in GitHub Actions and runs the end-to-end pilot.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Data Foundation** - ContactBrief model, HubSpot client, rate limiter, package scaffolding
- [ ] **Phase 2: Stage 1 Assemble** - Full research brief assembly from HubSpot data
- [ ] **Phase 3: Stages 2+3 Filter + Route** - Exclusion logic and vertical routing table
- [ ] **Phase 4: Stage 4 Generate** - Anthropic Batch API generation with 8-key JSON output
- [ ] **Phase 5: Stage 5+6 Lint + Assemble Bodies** - 18 lint checks, regeneration path, placeholder appending
- [ ] **Phase 6: Stage 7 Write-back + Notifications** - HubSpot write-back, Teams notifications, run summary
- [ ] **Phase 7: GitHub Actions Wiring + Pilot** - Two-job workflow, dry-run, 20-contact live pilot

## Phase Details

### Phase 1: Data Foundation
**Goal**: The pipeline's core data model, HubSpot client, and rate limiter exist and are smoke-tested against HubSpot
**Depends on**: Nothing (first phase)
**Requirements**: ASSM-09, INFRA-04, INFRA-05
**Success Criteria** (what must be TRUE):
  1. `pipeline/` package structure exists with all stage module stubs importable from `run.py`
  2. HubSpot client fetches a single contact record from the live portal without authentication errors
  3. Rate limiter enforces the 100 req/10s general limit and the 4 req/s CRM Search limit without hitting a 429 on a 20-request burst test
  4. GitHub Secrets (`HUBSPOT_TOKEN`, `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL`) are configured and the HubSpot client initializes from the secret without error
**Plans**: TBD

### Phase 2: Stage 1 Assemble
**Goal**: For each contact ID, the pipeline produces a complete, spec-compliant plain-text research brief ready for generation
**Depends on**: Phase 1
**Requirements**: ASSM-01, ASSM-02, ASSM-03, ASSM-04, ASSM-05, ASSM-06, ASSM-07, ASSM-08
**Success Criteria** (what must be TRUE):
  1. Running Stage 1 against 3-5 real Lane A contact IDs produces a brief file per contact with all §3.7 sections populated
  2. The handover name field resolves to the correct previous rep first name (or marks owner as active) for each test contact
  3. Bot-noise notes (job-ad alerts, enrichment notifications) are absent from the assembled brief; INTERNAL-tagged content appears in the brief with the INTERNAL label
  4. Junk deals (names starting with "(Test)", "(delete)", or containing "test" as a standalone token) are excluded from the deal list
  5. JP reviews the 3-5 assembled briefs and confirms they match spec §3 intent before Phase 3 begins
**Plans**: TBD

### Phase 3: Stages 2+3 Filter + Route
**Goal**: Every contact is either passed to generation with a routing assignment or written to the exclusion report with a filter code and reason
**Depends on**: Phase 2
**Requirements**: FILT-01, FILT-02, ROUT-01
**Success Criteria** (what must be TRUE):
  1. Running Stages 2+3 against a mixed test set produces an exclusion report listing all excluded contacts with their E1-E6 filter code and plain-English reason
  2. Each passing contact carries a `case_study_name`, `email_3_url`, and `email_4_url` resolved from the routing table (or the no-match fallback values)
  3. The exclusion report is formatted and ready for Teams posting; no excluded contact proceeds to Stage 4
**Plans**: TBD

### Phase 4: Stage 4 Generate
**Goal**: The pipeline submits all passing contacts to Anthropic Batch API and retrieves validated 8-key JSON output for each contact
**Depends on**: Phase 3
**Requirements**: GEN-01, GEN-02, GEN-03, GEN-04, GEN-05, GEN-06, GEN-07, GEN-08
**Success Criteria** (what must be TRUE):
  1. A batch submitted for 5 test contacts returns `processing_status == "ended"` and all 5 results are matched by `custom_id` with no unmatched or silently dropped results
  2. Each result parses as valid 8-key JSON (e1-e5 with subject+body, call1 body, call2 body, pin body) per the Pydantic schema
  3. The batch ID is written as a GitHub Actions artifact before the polling loop begins; a simulated job timeout does not lose the batch ID
  4. The system prompt uses `{type: ephemeral, ttl: 1h}` cache control; per-contact token usage is accumulated and a dollar-cost estimate is calculated for the run
  5. Hard batch errors (`invalid_request_error`) and soft errors (server errors) are routed to separate output files, not silently ignored
**Plans**: TBD

### Phase 5: Stage 5+6 Lint + Assemble Bodies
**Goal**: Every generated contact output passes all 18 lint checks (or is flagged for review), and assembled bodies contain no un-substituted placeholder brackets
**Depends on**: Phase 4
**Requirements**: LINT-01, LINT-02, LINT-03, LINT-04, LINT-05, BODY-01, BODY-02, BODY-03
**Success Criteria** (what must be TRUE):
  1. Deliberately injecting each of the 18 hard-failure patterns into a test output triggers the correct lint check and routes the contact to regeneration (one attempt) then to the review file on second failure
  2. Soft-warning contacts are added to the review file without blocking write-back
  3. The idempotency guard skips write-back for any contact where `email_1_subject` is already non-empty, unless `--force-regenerate` is passed
  4. Case study and booking link placeholders appear correctly in e2 and e5 bodies; any body containing a literal `[` character is rejected before reaching Stage 7
  5. Token usage (input, output, cache read, cache write) is accumulated per contact across the batch and a total run cost estimate is available in the run log
**Plans**: TBD

### Phase 6: Stage 7 Write-back + Notifications
**Goal**: All passing contacts are written to HubSpot with correct property types and all four Teams notifications are delivered at the right pipeline stages
**Depends on**: Phase 5
**Requirements**: WB-01, WB-02, WB-03, WB-04, WB-05, WB-06, REV-01, REV-02, REV-03, REV-04, REV-05
**Success Criteria** (what must be TRUE):
  1. The preflight check detects any email property that is `fieldType: text` (not `textarea`) and halts the pipeline with an actionable error before any write occurs
  2. Running against 3 sandbox contacts writes all 10 email properties, both call task note properties, and the pin note property; a read-back confirms values are non-empty and newlines are preserved
  3. The exclusion report Teams notification is sent and received in the correct Teams channel before generation begins; JP can read all E1-E6 contacts with their filter codes
  4. The post-generation review file (every 10th contact + soft-warning contacts) is sent to Teams; the run cost summary and failed contacts list are sent at run end
  5. Contacts that error in any stage are written to `failed_contacts.json` as a GitHub Actions artifact
**Plans**: TBD
**UI hint**: no

### Phase 7: GitHub Actions Wiring + Pilot
**Goal**: The complete pipeline runs end-to-end from a HubSpot workflow_dispatch trigger through to HubSpot write-back, verified on a 20-contact live pilot
**Depends on**: Phase 6
**Requirements**: INFRA-01, INFRA-02, INFRA-03
**Success Criteria** (what must be TRUE):
  1. Triggering the workflow with a 20-contact JSON payload from HubSpot fires both the `prepare` and `complete` jobs in sequence; the batch ID artifact is visible in the Actions run
  2. Dry-run mode completes the full 20-contact run (Stages 1-5) without writing to HubSpot; the operator can inspect all generated and linted output
  3. Live pilot run writes all 20 contacts to HubSpot; a manual spot-check of 3 contacts confirms email properties, call note properties, and pin note are populated correctly with no stripped newlines
  4. The GITHUB_STEP_SUMMARY displays contacts processed / excluded / failed / written counts; all four Teams notifications arrive in the correct channel
  5. The `workflow_dispatch` payload size for 20 contacts is logged; if the 1,024-char per-input limit would be hit at 422 contacts, the operator is notified and `repository_dispatch` is evaluated as a fallback
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Data Foundation | 0/TBD | Not started | - |
| 2. Stage 1 Assemble | 0/TBD | Not started | - |
| 3. Stages 2+3 Filter + Route | 0/TBD | Not started | - |
| 4. Stage 4 Generate | 0/TBD | Not started | - |
| 5. Stage 5+6 Lint + Assemble Bodies | 0/TBD | Not started | - |
| 6. Stage 7 Write-back + Notifications | 0/TBD | Not started | - |
| 7. GitHub Actions Wiring + Pilot | 0/TBD | Not started | - |
