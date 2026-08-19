# Research Summary -- Staff Domain Re-Engagement Pipeline

**Synthesized:** 2026-08-19
**Sources:** STACK.md, FEATURES.md, ARCHITECTURE.md, PITFALLS.md
**Overall confidence:** HIGH -- all four research files verified against official documentation

---

## Executive Summary

This is a 7-stage production AI generation pipeline: HubSpot reads to exclusion filter to vertical routing to Anthropic Batch API generation to 18-check lint to body assembly to HubSpot write-back, triggered by a HubSpot workflow via GitHub Actions workflow_dispatch. The recommended implementation uses Python 3.12 with anthropic 0.122.0, hubspot-api-client 12.0.0, and Pydantic v2 for LLM output validation. The architecture splits into two GitHub Actions jobs: a prepare job (Stages 1-3 plus batch submit, 20-minute timeout) and a complete job (batch poll plus Stages 5-7 plus Teams notify, 90-minute timeout), with a JSON artifact carrying the batch ID and assembled contact data between jobs. This split is the key architectural recommendation: it is not required by the 6-hour GitHub Actions limit but provides a clean re-run boundary if write-back partially fails.

Three spec bugs require immediate correction before Phase 1 build begins. First, the spec calls for cache_control: {type: ephemeral} which now has a 5-minute TTL (changed March 2026); for Batch API jobs this must be {type: ephemeral, ttl: 1h} or cache hits degrade to near-zero and cost projections break. Second, the Teams webhook must use a Power Automate URL -- the legacy webhook.office.com connector was retired 2026-03-31 and will not work. Third, the workflow_dispatch per-input character limit is 1,024 characters; a JSON array of 422 HubSpot contact IDs is approximately 5,500 characters and may silently truncate when passed through the HubSpot workflow trigger, requiring a pilot verification or a switch to repository_dispatch.

Five features that the spec does not define are table stakes for a production pipeline and must be built in Phase 1: an idempotency guard (check whether email_1_subject is already set before writing, to prevent double-writes on retry), a failed_contacts.json artifact for partial failure resume, Batch API error triage (routing invalid_request_error separately from server errors), a HubSpot 429 retry decorator with exponential backoff, and a JSONL run log with per-contact token cost tally. These are uniformly low-to-medium complexity and collectively protect against the three most likely production failures: data corruption on re-run, silent cost creep, and no recovery path from a partial batch failure.

---

## Key Findings

### From STACK.md

| Technology | Version | Decision |
|------------|---------|----------|
| Python | 3.12 | LTS; full ecosystem support |
| anthropic SDK | 0.122.0 | Official SDK; use client.messages.batches.* directly |
| hubspot-api-client | 12.0.0 | Only stable HubSpot Python client; hubspot-sdk 0.1.0a9 is pre-release alpha -- do not use |
| Pydantic v2 | 2.13.4 | model_validate_json() parses and validates the 8-key LLM output in one call; dataclasses and TypedDict do not validate at runtime |
| requests | 2.32.x | Teams webhook only; already a transitive dependency |

Critical version requirement: hubspot-api-client 12.0.0 is the only production-ready HubSpot Python client. The hubspot-sdk package is a pre-release alpha and must not be used.

Secrets: ANTHROPIC_API_KEY, HUBSPOT_ACCESS_TOKEN, TEAMS_WEBHOOK_URL stored as GitHub repository secrets. The anthropic SDK reads ANTHROPIC_API_KEY automatically; HubSpot client requires explicit token pass.

### From FEATURES.md

**Table stakes -- build in Phase 1 (all missing from spec):**

1. Structured JSONL run log per contact (contact ID, stage reached, lint result, regenerated Y/N, write status, tokens)
2. Idempotency guard -- check email_1_subject non-empty before writing; skip unless --force-regenerate flag set
3. Batch API error triage -- invalid_request_error routes to malformed_contacts.json (do not retry); server error routes to failed_contacts.json (retry safe)
4. HubSpot 429 retry decorator -- exponential backoff, honour Retry-After header, cap at 5 retries
5. Partial failure resume via failed_contacts.json artifact
6. GITHUB_STEP_SUMMARY run summary (contacts processed / excluded / failed / written)
7. Per-contact cost tally from message.usage fields

**Spec bug confirmed:** cache_control: {type: ephemeral} uses the 5-minute TTL. Fix to {type: ephemeral, ttl: 1h} for Batch API calls. Confidence: HIGH (official Anthropic docs, March 2026 TTL change confirmed).

**Differentiators for Phase 2:**
- Spec version stamped on each contact as a HubSpot property
- Lint metrics in run summary (hard failures, regeneration rate, soft warning count)
- Routing table coverage report (what % of contacts hit each row vs fallback)
- Dry-run cost estimate from 10-contact sample before full submit

**Anti-features (deliberately avoid):**
- External observability platforms (Langsmith, Helicone) -- quarterly pipeline, not a live service
- Dead letter queues (SQS/Redis) -- failed_contacts.json is sufficient
- Database-backed run state -- GitHub Actions artifacts are the correct pattern
- Unlimited lint retry -- the spec two-pass ceiling is correct

### From ARCHITECTURE.md

**Two-job workflow is the recommended pattern:**

- Job 1 (prepare, timeout 20 min): Stages 1-3, batch submit, artifact write (batch_id + ContactBrief JSON)
- Job 2 (complete, timeout 90 min): batch poll, Stages 5-7, Teams notify

**Package structure** -- not a single file; 7 stages require modular organisation:
- pipeline/models.py: ContactBrief + PipelineResult dataclasses
- pipeline/lanes/: LaneConfig dataclass + lane_a.py
- pipeline/stages/: one module per stage
- pipeline/hubspot/client.py: thin wrapper around hubspot-api-client
- pipeline/notifications.py: Teams webhook formatter
- pipeline/run.py: entrypoint and orchestrator

**Central data carrier:** ContactBrief dataclass (stdlib, no dependency). Pydantic used only at the HubSpot API boundary to validate raw JSON responses. ContactBrief is immutable-by-convention after Stage 3.

**Stage 7 write-back sequence per contact (confirmed, not speculative):**
1. POST /crm/v3/objects/notes to create note, capture note_id
2. POST note-to-contact association via /crm/v4/associations/notes/contacts/batch/create (association type ID 202)
3. Batch PATCH contact: 10 email properties + hs_pinned_engagement_id: note_id

Steps 1 and 3 cannot be combined. HubSpot validates the association on render (not on write), so a PATCH referencing an unassociated engagement returns 200 but produces a silently broken pin. Add a read-back verify after the PATCH.

**Lane abstraction:** LaneConfig dataclass injected into generate stage. Lane B requires only a new lanes/lane_b.py file -- zero changes to shared stages.

**Error handling:** Continue-and-collect pattern per contact. Only fail fast at run level if Stage 1 fails for all contacts (e.g., HubSpot auth failure).

### From PITFALLS.md

**Critical pitfalls (silent corruption or blocked run):**

| ID | Pitfall | Prevention |
|----|---------|------------|
| C1 | HubSpot rate limit burst -- ~3,000 total calls per run; no built-in backoff | Exponential backoff decorator; sequential processing in Stage 1 |
| C2 | CRM Search API separate 4 req/sec limit (stricter than general limit) | Use associations endpoint not search for notes/engagements per contact |
| C3 | Batch ID lost if GitHub Actions job times out before ID is persisted | Persist batch_id to artifact immediately after batches.create(), before polling loop |
| C4 | Note pin silently fails if association step is skipped | Three-step sequence (create, associate, PATCH); read-back verify hs_pinned_engagement_id |
| C5 | cache_control: ephemeral now 5-min TTL (changed March 2026) -- breaks batch cost model | Use {type: ephemeral, ttl: 1h} for batch requests |
| C6 | Email body properties as fieldType: text accept writes but strip newlines at render | Preflight GET on all 10 email properties; assert fieldType == textarea before any write |

**Moderate pitfalls:**

| ID | Pitfall | Prevention |
|----|---------|------------|
| M1 | workflow_dispatch per-input limit is 1,024 chars; 422 IDs ~5,500 chars may truncate | Verify ID count in first pilot log; if truncating, switch to repository_dispatch |
| M2 | Task engagements created with no owner if contact_owner_id not threaded Stage 1 to Stage 7 | Carry contact_owner_id through all data models; assert non-null after task creation |
| M3 | LLM passes JSON validation but leaks INTERNAL content via paraphrase not exact substring | Token-overlap check against INTERNAL bullets; distinctive noun extraction |
| M4 | Batch results are unordered; partial failures may be silently ignored | Always match by custom_id; assert result count equals submitted count |
| M5 | Close bank verbatim check triggers both lint check 2 and 9 on same contact -- regeneration loop | Strip punctuation before close comparison; paste exact close in regeneration prompt |

---

## Implications for Roadmap

Research findings drive a 7-phase build order with clear dependencies. Phases 1-3 build the data foundation and deterministic pipeline stages. Phase 4 introduces the LLM integration, starting with realtime API before Batch API to validate the generation loop without batch complexity. Phases 5-6 build lint and write-back with full sandbox testing. Phase 7 wires up GitHub Actions and runs the pilot.

### Suggested Phase Structure

**Phase 1 -- Data Foundation + HubSpot Client**
- Rationale: nothing else can be tested without ContactBrief and a working HubSpot client
- Delivers: models.py, hubspot/client.py, smoke test against HubSpot sandbox with one known contact ID
- Key pitfall to avoid: C2 (use associations endpoint, not search)
- Note: build 429 retry decorator here -- it protects all subsequent stages

**Phase 2 -- Stage 1: Assemble**
- Rationale: most complex stage; must be validated by JP before generation begins
- Delivers: all 6 section builders (colleagues, deals, notes, handover, geography, close assignment); bot-noise filter; readable brief for 3-5 real Lane A contacts
- Key pitfalls to avoid: C1 (sequential processing), m1 (inactive owner check), M2 (carry contact_owner_id through data model)
- Research flag: JP brief review is a validation gate; do not proceed to Phase 3 without sign-off

**Phase 3 -- Stages 2 + 3: Filter + Route**
- Rationale: deterministic logic; unit-testable; exclusion report needed before generation
- Delivers: stages/filter.py (E1-E6), stages/route.py (vertical lookup), exclusion report formatter
- Key pitfall to avoid: none critical; well-defined spec logic

**Phase 4 -- Stage 4: Generate (realtime first, then Batch API)**
- Rationale: validate generation output against spec before committing to batch infrastructure
- Delivers: lanes/lane_a.py LaneConfig, realtime generation path, Pydantic validation of 8-key output, then Batch API path with two-job artifact handoff
- Spec bugs to fix before this phase: change cache_control ephemeral TTL to 1h for batch requests; persist batch_id to artifact before polling loop
- Key pitfalls to avoid: C3 (batch ID persistence), C5 (TTL), M4 (custom_id matching, result count assertion)
- Table stakes to build in this phase: Batch API error triage, JSONL run log, per-contact cost tally

**Phase 5 -- Stage 5: Lint**
- Rationale: build after first real outputs exist to test against
- Delivers: all 18 hard failure checks, 5 soft warning checks, regeneration path (two-pass ceiling), cross-contact variety check
- Key pitfalls to avoid: M3 (INTERNAL content token-overlap check), M5 (close bank punctuation tolerance)
- Table stakes to build in this phase: idempotency guard (set up before Stage 7 is wired)

**Phase 6 -- Stages 6 + 7: Assemble Bodies + Write-Back**
- Rationale: final integration; test against HubSpot sandbox before pilot; most failure modes are silent
- Delivers: placeholder appending, pre-send bracket guard, note create-associate-PATCH sequence, call task creation, batch PATCH for email properties
- Critical preflight to implement: GET all 10 email properties, assert fieldType == textarea -- halt with clear error if wrong (Pitfall C6)
- Key pitfalls to avoid: C4 (note pin three-step sequence + read-back verify), C6 (property fieldType), M2 (owner assignment on tasks), m3 (duplicate task guard)
- Teams webhook must use Power Automate URL -- verify before this phase (legacy webhook.office.com retired 2026-03-31)
- Verify multi-line text rendering in HubSpot sequence editor on 3 records before full run (Pitfall m2)

**Phase 7 -- GitHub Actions Wiring + Pilot Run**
- Rationale: wrap the complete working pipeline in infrastructure; final integration test
- Delivers: run_pipeline.yml two-job workflow, GITHUB_STEP_SUMMARY, Teams notification with run cost and exclusion breakdown, end-to-end pilot (20 contacts, dry-run first, then live)
- workflow_dispatch vs repository_dispatch decision: verify first pilot trigger logs all expected contact IDs; if truncation detected at 1,024-char limit (Pitfall M1), switch to repository_dispatch
- Teams webhook: confirm Power Automate URL is in place before this phase

### Research Flags

| Phase | Research Need | Reason |
|-------|--------------|--------|
| Phase 2 | JP brief review gate | Stage 1 output must match spec intent; a misbuilt brief propagates errors through all downstream stages |
| Phase 4 | Batch API pilot on real 10-contact run | Verify cache hit rates in cache_read_input_tokens; confirm cost model before scaling to 422 |
| Phase 6 | HubSpot sandbox write-back verification | Note pinning confirmed in principle but must be verified in the specific HubSpot portal; fieldType preflight is a live API check |
| Phase 7 | workflow_dispatch vs repository_dispatch | 1,024-char per-input limit requires empirical verification; cannot be resolved from docs alone |

Phases 1, 3, and 5 follow well-documented patterns and do not require additional research passes.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack choices | HIGH | All versions verified on PyPI; hubspot-sdk pre-release status confirmed |
| Anthropic Batch API behaviour | HIGH | Official docs verified 2026-08-19; result types, polling pattern, cache TTL change all confirmed |
| cache_control TTL bug | HIGH | March 2026 TTL change documented; official Anthropic docs confirm ttl: 1h as the correct parameter |
| HubSpot note pinning sequence | HIGH | Confirmed via official HubSpot community thread; association type ID 202 confirmed |
| HubSpot property fieldType requirement | HIGH | Official HubSpot Properties API docs; confirmed critical pitfall |
| Teams webhook retirement | MEDIUM-HIGH | Multiple sources confirm webhook.office.com retirement 2026-03-31; Power Automate replacement confirmed |
| workflow_dispatch 1,024-char per-input limit | MEDIUM | Community-sourced; programmatic API triggers may differ from UI triggers -- requires pilot verification |
| Two-job GitHub Actions pattern | MEDIUM | Based on GitHub Actions best practices and architectural reasoning; no single authoritative source mandates this split |
| HubSpot rate limit arithmetic | MEDIUM | Official limits confirmed; real-world burst behaviour at 422 contacts is projective, not empirically verified |

---

## Gaps Requiring Attention During Planning

1. **workflow_dispatch vs repository_dispatch decision** -- must be resolved empirically on first pilot trigger by logging len(json.loads(contact_ids)) and comparing to expected count. The 5,500-character payload from 422 IDs exceeds the documented 1,024-char per-input limit; whether the HubSpot workflow API trigger enforces this limit is unconfirmed.

2. **Note pinning sandbox verification** -- the three-step sequence (create, associate, PATCH) is confirmed in principle from community documentation, but the specific HubSpot portal (Staff Domain) must be tested in sandbox before pilot.

3. **Multi-line text rendering in sequence editor** -- the HubSpot knowledge base notes that newline breaks in multi-line text properties are not supported in certain tools including sequences. Empirical test on 3 records in the HubSpot sequence editor is required before full run.

4. **Actual cost per run** -- the 2-3 dollar estimate assumes high cache hit rates (>70%) with the 1-hour TTL. Per-contact cost tally from the first real pilot run is the only way to verify.

5. **HubSpot portal tier** -- rate limit arithmetic assumes Free/Starter (100 req/10 sec). If the Staff Domain portal is on Pro/Enterprise (200 req/10 sec), headroom doubles. Confirm portal tier before building the rate limit strategy.

---

## Spec Corrections Summary

These are confirmed bugs in the current spec (v1.0) that must be fixed before build begins:

| Bug | Location in Spec | Correct Value | Confidence |
|-----|-----------------|---------------|------------|
| cache_control: {type: ephemeral} uses 5-min TTL, breaks batch caching | Stage 4 definition | {type: ephemeral, ttl: 1h} | HIGH |
| Teams webhook URL type unspecified | Stage 7 / notify step | Must be Power Automate URL, not webhook.office.com | HIGH |
| Note pinning described as unconfirmed | Stage 7 key decision | Confirmed: 3-step sequence (create, associate, PATCH) with read-back verify | HIGH |
| No idempotency strategy defined | Missing entirely | Check email_1_subject non-empty before write; skip unless --force-regenerate | HIGH |
| No partial failure resume defined | Missing entirely | failed_contacts.json artifact; pipeline accepts list ID or contact file as input | HIGH |
| No cost reporting defined | Missing entirely | Accumulate message.usage per batch result; log to run summary | MEDIUM |

---

## Sources (Aggregated)

- Anthropic Batch Processing docs: https://platform.claude.com/docs/en/build-with-claude/batch-processing (verified 2026-08-19)
- Anthropic prompt caching docs: https://platform.claude.com/docs/en/build-with-claude/prompt-caching (verified 2026-08-19)
- Anthropic prompt cache TTL change March 2026: https://dev.to/whoffagents/anthropic-silently-dropped-prompt-cache-ttl-from-1-hour-to-5-minutes-16ao
- anthropic SDK on PyPI: https://pypi.org/project/anthropic/ (version 0.122.0, 2026-08-13)
- hubspot-api-client on PyPI: https://pypi.org/project/hubspot-api-client/ (version 12.0.0, 2025-05-07)
- hubspot-sdk on PyPI: https://pypi.org/project/hubspot-sdk/ (version 0.1.0a9, pre-release alpha)
- Pydantic on PyPI: https://pypi.org/project/pydantic/ (version 2.13.4, 2026-05-06)
- HubSpot note pinning community confirmed: https://community.hubspot.com/t5/APIs-Integrations/Pin-a-note-with-engagement-API/m-p/413062
- HubSpot property fieldType values: https://developers.hubspot.com/docs/api-reference/legacy/crm/properties/guide
- HubSpot API rate limits: https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
- HubSpot CRM Search API rate limit: https://developers.hubspot.com/changelog/crm-search-api-rate-limit-increase
- HubSpot multi-line text in sequences: https://community.hubspot.com/t5/Account-Settings/Spacing-for-multi-line-text-properties-used-as-personalized/m-p/399987
- GitHub Actions workflow_dispatch 25 inputs: https://github.blog/changelog/2025-12-04-actions-workflow-dispatch-workflows-now-support-25-inputs/
- GitHub Actions payload size: https://github.com/orgs/community/discussions/120093
- GitHub Actions timeout: https://docs.github.com/en/actions/reference/actions-limits
- Teams webhook Office 365 Connectors retirement 2026-03-31: multiple sources confirmed
