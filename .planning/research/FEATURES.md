# Feature Landscape: AI Email Generation Pipeline

**Domain:** Production AI generation pipeline (B2B outreach, LLM batch processing, CRM write-back)
**Researched:** 2026-08-19
**Research scope:** What well-built production AI pipelines have that this spec may not have considered — observability, cost tracking, partial failure handling, rate limiting, idempotency, output versioning.

---

## Context: What the Spec Already Has

Before categorising gaps, here is what the spec has covered that other pipelines often miss:

- Hard lint with regeneration on failure (18 checks, two-pass model call)
- Pre-send guard for literal `[` placeholders
- Exclusion report (contacts go to report, not silently dropped)
- Review file for every Nth contact + all soft warnings
- Dry-run mode
- Batch API for full runs (50% cost reduction)
- Shared core / lane-config separation for future lanes

These are production-quality design decisions. The gaps identified below are additive, not corrective.

---

## Table Stakes

Features that every production AI generation pipeline needs. Missing any of these means the pipeline is unreliable, unauditable, or unsafe to run on real CRM data.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Structured run log (JSON file per run)** | Without a durable record of what happened, debugging failures is guesswork. "What did it generate for contact X?" must be answerable after the fact. | Low | Write one JSON record per contact: contact ID, stage reached, lint result, regenerated (Y/N), HubSpot write status, tokens used. Append to a JSONL file per run. |
| **Run-level summary on GITHUB_STEP_SUMMARY** | GitHub Actions surfaces $GITHUB_STEP_SUMMARY in the workflow UI. A one-page Markdown table — contacts processed / excluded / failed / written — gives JP a glance-view without reading logs. | Low | Write to `$GITHUB_STEP_SUMMARY` at the end of the run. Include counts, cost estimate, any hard failures. |
| **Per-contact cost tally** | The spec estimates ~$2–3 per 500-contact run. That estimate needs to be verified and tracked run-by-run. Token counts are available in every Anthropic API response (`usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_input_tokens`). | Low | For Batch API: token counts are in each result's `message.usage` object. Accumulate across all succeeded results, multiply by current per-MTok rates. Log to run summary. |
| **Idempotency guard: skip already-written contacts** | If the workflow is re-triggered (GitHub Actions double-trigger, manual retry after partial failure), contacts already written to HubSpot must not receive a second write. Writing twice overwrites potentially human-edited properties with regenerated copy — the wrong direction. | Medium | Strategy: before Stage 4 (generation), check whether the target HubSpot property (`email_1_subject`) is already non-empty for each contact. If non-empty, skip unless a `--force-regenerate` flag is set. This is the upsert-not-insert pattern and costs one extra read per contact (or a batch read up front). |
| **Partial failure resume: failed contacts list** | If 50 of 500 contacts fail (API error, lint hard-failure after two passes, HubSpot write error), there must be a way to rerun only those 50 without triggering the full list again. Without this, a partial failure means either rerunning everything (wastes cost, risks double-writes) or manually editing the input payload. | Medium | Write a `failed_contacts.json` file as a run artifact. Format: `[{"contact_id": "...", "stage": "GENERATE", "error": "..."}]`. The next invocation accepts this file as the input instead of a list ID — same pipeline, different input source. |
| **Anthropic Batch API error triage** | The Batch API returns four per-request result types: `succeeded`, `errored`, `canceled`, `expired`. The `errored` type splits further: `invalid_request_error` (do NOT retry — fix the payload) vs server errors (safe to retry). Treating all errors the same causes either infinite retry loops or silent data loss. | Low | In the batch result processing loop, route by error subtype. Server errors go to `failed_contacts.json` for retry. `invalid_request` errors go to a separate `malformed_contacts.json` — these need code investigation, not retry. |
| **HubSpot write-back rate limit guard** | HubSpot Private App: 100 requests per 10 seconds on Starter, 200 on Pro/Enterprise. Batch update (100 contacts per request) counts as 1 request. At 500 contacts = 5 batch requests, this is well within limits — but the pipeline must handle 429 responses with exponential backoff + jitter, not crash. | Low | Wrap all HubSpot API calls in a retry decorator: initial delay 1s, exponential backoff, cap at 5 retries. Respect the `Retry-After` header if present. This is a one-time implementation that protects all stages. |
| **Pre-write content validation** | The spec has a pre-send `[` bracket check. This is correct. It belongs in Stage 7 (write-back), not at sequence enrolment. Any email body containing a literal `[` must block the write for that contact and route it to the review file, not silently write it to HubSpot. | Low | Already implied by the spec; make it explicit: the bracket check is a hard gate on HubSpot write, not a post-write audit. |

---

## Differentiators

Features that add meaningful resilience or observability without being essential for a first run. These are "build in Phase 2 if Phase 1 surfaced pain."

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Spec version stamped on every contact** | When the copy rules change (spec v1.1 already exists), you need to know which contacts were generated under which version. Without this, JP can't tell whether a flagged email reflects the old rules or the new ones. | Low | Write a `pipeline_spec_version` custom contact property in HubSpot (single-line text). Set it to the spec version tag (e.g., `LaneA-v1.0`) at write-back time. This is one extra property per contact per write. |
| **Batch ID stored per contact** | The Anthropic Batch API returns a `batch_id` for every run. Storing this in a HubSpot property (or the run log) lets you retrieve the exact generation output from Anthropic's console for 29 days after the run, without needing your own log. | Low | Store in the run log (not necessarily in HubSpot — that's overkill for 500 contacts). But log `batch_id` prominently in the run summary so it's retrievable if a contact's output needs to be re-examined. |
| **Lint metrics in run summary** | Tracking the lint pass rate over time (what % of contacts needed regeneration, what % had soft warnings) tells you when the system prompt is drifting or when a new edge case is appearing. After 5 runs, patterns become visible. | Low | Add counters to the run log: `lint_hard_failures`, `lint_regenerated`, `lint_soft_warnings_count`. Include in GITHUB_STEP_SUMMARY. |
| **Routing table coverage report** | What % of contacts hit each industry row? What % hit the fallback (no-match) row? If 40% of contacts are falling through to the default Bells Pure Ice case study, the routing table may need expansion. | Low | One-time pass after Stage 3. Count contacts per routing row. Include in run summary. No code complexity; just a counter per row. |
| **1-hour cache TTL for Batch API system prompt** | The spec uses `cache_control: {type: "ephemeral"}` (5-minute TTL). For Batch API runs, the batch typically completes in under 1 hour but can take longer. If processing exceeds 5 minutes, the cached system prompt expires and each subsequent request pays full input token price. Anthropic explicitly recommends using the 1-hour TTL (`"ttl": "1h"`) for batch jobs. | Low | Change `cache_control: {type: "ephemeral"}` to `cache_control: {type: "ephemeral", "ttl": "1h"}` for Batch API calls. The system prompt is ~600 tokens; at 500 contacts, a cache miss on every contact after the 5-minute window adds ~$0.30 to the run cost. |
| **Dry-run cost estimate before full run** | Before submitting a 500-contact batch, estimate the cost from a 10-contact sample. "This run will cost approximately $2.80" is actionable. "The run completed and cost $2.80" is historical. | Medium | Run Stage 1–3 on all contacts, count total brief token lengths (approximate), multiply by Batch API rates. Print the estimate and require confirmation (or `--no-confirm` flag) before submitting the batch. |
| **Teams notification includes run cost** | The existing Teams webhook notification covers the review file. Adding the run cost and contact counts (processed / excluded / failed) to this notification means JP sees the full run picture in one Teams message without opening GitHub. | Low | Extend the existing Teams webhook payload. Complexity is near-zero since the webhook call already exists; it just needs more data. |
| **Exclusions categorised by filter type in Teams message** | The current spec sends "exclusions report" to Teams. Reporting the breakdown (E1: 12, E2: 3, E3: 7…) is more actionable than a raw count — JP can see whether most exclusions are "already active" (E1) vs "no history" (E5) and adjust the list sourcing accordingly. | Low | Counters per exclusion type are trivially computed in Stage 2. Include in Teams message and GITHUB_STEP_SUMMARY. |

---

## Anti-Features

Things to deliberately NOT build. These are patterns that look reasonable for "a production pipeline" but are overkill, add maintenance burden, or misfit this pipeline's actual scale and context.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| **External observability platform (Langsmith, Helicone, MLflow)** | This pipeline runs ~4 times per year on ~500 contacts. It is not a live web service with concurrent users or real-time latency requirements. Integrating a third-party observability layer adds a dependency, a billing account, and a data-sharing arrangement for a pipeline that runs quarterly. | Write structured JSONL logs as GitHub Actions artifacts. They are retained for 90 days by default and require zero infrastructure. |
| **Dead letter queue (SQS, Redis, etc.)** | A DLQ is appropriate when failed items need async retry or human review across multiple systems. This pipeline's "dead letters" are just the contacts in `failed_contacts.json` — a file that the operator reads and reruns manually. Adding a message queue for 50 failed contacts in a 500-contact run is architectural overkill. | `failed_contacts.json` as a run artifact is sufficient. JP or the operator reviews it and re-triggers the workflow with that file as input. |
| **Database-backed run state (Postgres, SQLite, etc.)** | GitHub Actions has no persistent storage between runs. A file-based checkpoint or artifact solves the resume problem without requiring a database. Introducing a database creates a hosting requirement, schema management, and a new class of "DB connection failed" failures. | Run artifacts (JSON files uploaded via `actions/upload-artifact`) are the correct GitHub Actions pattern for inter-run state. |
| **Streaming generation (realtime progress per contact)** | Streaming makes sense for interactive applications where users watch responses appear. This pipeline runs unattended; the operator sees the result when the job finishes. Streaming adds connection management complexity and prevents use of the Batch API (which is explicitly not compatible with streaming). | Batch API for full runs; realtime (non-streaming) for dev/iteration. The spec already makes this right call. |
| **Auto-retry lint failures without limit** | The spec already defines "regenerate once, then flag." Adding unlimited retries risks infinite loops on a consistently broken model output, burning tokens and time. Two-pass (generate → fail → regenerate once → flag) is the correct ceiling. | The existing two-pass lint is sufficient. Trust the lint to catch problems; trust JP to review the flagged outputs. |
| **Per-contact branching on generation model** | "Use a cheaper model for simple records, Sonnet 5 for complex ones" sounds efficient but creates two code paths, inconsistent output quality, and makes quality regression harder to attribute. The cost difference at 500 contacts is under $1. | Use claude-sonnet-5 for all contacts. The spec is already correct here. |
| **Full MLOps prompt versioning platform (DVC, MLflow registry)** | Prompt versioning tools are designed for teams iterating rapidly on many prompts across many experiments. This pipeline has one locked system prompt per spec version, managed in a single Markdown file under Git. Git IS the version control. | Tag spec versions in Git (e.g., `spec-LaneA-v1.0`). Store the spec version string in the run log. That is sufficient traceability for this use case. |

---

## Feature Dependencies

```
Structured run log (JSONL per contact)
  └── enables: per-contact cost tally
  └── enables: lint metrics in run summary
  └── enables: run summary on GITHUB_STEP_SUMMARY
  └── enables: Teams notification with full run data

Idempotency guard (check before write)
  └── requires: a reliable "was this contact already processed?" signal
  └── recommended signal: non-empty `email_1_subject` property in HubSpot
  └── alternative signal: `pipeline_spec_version` property being set

Partial failure resume (failed_contacts.json)
  └── requires: structured run log (to know which contacts failed at which stage)
  └── requires: pipeline input to accept either a list ID or a contact list file
  └── enables: safe rerun without risk of double-write (depends on idempotency guard)

Anthropic Batch API error triage
  └── requires: streaming result processing (already the Anthropic SDK default)
  └── feeds: failed_contacts.json (server errors) vs malformed_contacts.json (invalid_request)

1-hour cache TTL
  └── independent change, no dependencies
  └── prevents silent cost increase on batches that process for > 5 minutes
```

---

## MVP Recommendation

Build all Table Stakes features in Phase 1. They are uniformly Low-to-Medium complexity and collectively protect the pipeline from the three most likely production failures: double-writes on retry, silent cost creep, and no way to recover from partial batch failures.

Prioritise in this order:

1. **Structured JSONL run log** — everything else reads from this
2. **Idempotency guard** — prevents data corruption on re-runs
3. **Batch API error triage** — prevents retry loops and data loss from treating all errors the same
4. **HubSpot 429 retry decorator** — one implementation protects all stages
5. **partial failure resume file (`failed_contacts.json`)** — safe recovery path
6. **GITHUB_STEP_SUMMARY run summary** — JP-facing operational view
7. **Per-contact cost tally** — verify the $2–3 estimate is accurate on first real run

Defer to Phase 2:

- Spec version property in HubSpot (useful but not blocking)
- 1-hour cache TTL fix (low cost impact at current scale, but do it before Lane B adds volume)
- Routing table coverage report (useful after first real run surfaces data)
- Dry-run cost estimate (useful quality-of-life, not blocking)

---

## Spec Gaps Identified

These are specific issues found during research that the current spec (v1.0) does not address:

**Gap 1: Prompt cache TTL mismatch with Batch API**
The spec calls for `cache_control: {type: "ephemeral"}` which has a 5-minute TTL. Anthropic's own documentation explicitly recommends using `"ttl": "1h"` for batch jobs because batches take longer than 5 minutes to process. A full 500-contact batch running for 45 minutes would see cache misses on all but the first few requests without this change. Fix: `cache_control: {type: "ephemeral", "ttl": "1h"}`.
Confidence: HIGH (from official Anthropic Batch API docs, 2026).

**Gap 2: No idempotency strategy defined**
The spec defines the write-back logic but does not address what happens if the workflow is triggered twice for the same contact list (double-trigger from HubSpot, operator retry after partial failure). The pipeline currently would overwrite HubSpot properties with freshly generated copy, discarding any human edits made post-first-run.

**Gap 3: Partial failure resume is not specified**
The spec defines lint hard failure handling (regenerate once, then flag) but does not define what happens if Stage 7 (HubSpot write) partially fails — e.g., 450 contacts written, then a sustained 429 terminates the run. The 50 unwritten contacts have no recovery path defined.

**Gap 4: Batch API result types not mapped to pipeline logic**
The Batch API returns `errored` (subdivided into `invalid_request` vs server error), `expired`, and `canceled`. The spec does not specify how each of these maps to pipeline behaviour. `expired` contacts (batch exceeded 24 hours) are a real risk for a 500-contact batch submitted during peak Anthropic load and must route to a failed list, not be silently ignored.

**Gap 5: No cost reporting**
The spec acknowledges a cost estimate (~$2–3) but does not define where or how actual cost is reported. Without per-run cost data, there is no way to detect a cost regression if the system prompt grows, average brief length increases, or a bug triggers repeated regeneration.

---

## Sources

- Anthropic Message Batches API official documentation: https://platform.claude.com/docs/en/build-with-claude/batch-processing (HIGH confidence — official, current)
- Anthropic prompt caching documentation: https://platform.claude.com/docs/en/build-with-claude/prompt-caching (HIGH confidence — official, current)
- HubSpot API rate limits production guide: https://www.scopiousdigital.com/blog/hubspot-api-rate-limits-production (MEDIUM confidence — third-party verified against HubSpot developer docs)
- HubSpot batch contact update limits (100 per request, 1 API call): https://community.hubspot.com/t5/APIs-Integrations/Limit-for-contact-batch-API/m-p/1143019 (MEDIUM confidence — community-sourced, consistent with HubSpot developer docs)
- Fault-tolerant AI agent pipelines — idempotency, retries, checkpoints: https://mightybot.ai/blog/fault-tolerant-ai-agent-pipelines/ (MEDIUM confidence — practitioner source, patterns consistent with Anthropic SDK docs)
- Idempotent AI agent retry-safe patterns: https://www.buildmvpfast.com/blog/idempotent-ai-agent-retry-safe-patterns-production-workflow-2026 (MEDIUM confidence — practitioner source)
- GitHub Actions job summaries: https://github.blog/news-insights/product-news/supercharging-github-actions-with-job-summaries/ (HIGH confidence — official GitHub blog)
- Anthropic rate limit handling (429/529): https://www.respan.ai/articles/anthropic-api-rate-limits (MEDIUM confidence — third-party, consistent with Anthropic docs)
