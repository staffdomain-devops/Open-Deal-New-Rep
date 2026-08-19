# Staff Domain Re-Engagement Pipeline

## Project Context

This is the Staff Domain Lane A Re-Engagement Pipeline — a Python pipeline on GitHub Actions that generates personalized 7-touch re-engagement sequences for past prospects using Claude Sonnet 5. It reads from HubSpot, generates 8 deliverables per contact (5 emails + 2 call task notes + 1 pin note), lints the output, and writes back to HubSpot contact properties.

**Core value:** Every email reads like someone went through the file — personalization changes the reason for each touch, not just the name.

**Key specs (read before building anything):**
- `SD_Reengagement_LaneA_Build_Spec.md` — v1.0 pipeline spec (system prompt, voice rules, lint checks, routing table, close bank)
- `New SDR - Deal old deal outreach.md` — v1.1 amendment (adds call task notes, pin note, 7-touch cadence, lint checks 13–18)
- `.planning/PROJECT.md` — living project context
- `.planning/REQUIREMENTS.md` — 44 v1 requirements with REQ-IDs
- `.planning/ROADMAP.md` — 7-phase build plan

## GSD Workflow

This project uses the GSD (Get Shit Done) workflow. Always check `.planning/STATE.md` for current position before starting work.

**Phase planning:** `/gsd-plan-phase N`
**Phase discussion:** `/gsd-discuss-phase N`
**Execution:** `/gsd-execute-phase N`
**Verification:** `/gsd-verify-work`

## Critical Constraints

### The spec is locked
The system prompt in `SD_Reengagement_LaneA_Build_Spec.md §5.2` must be used **verbatim**. Do not paraphrase, "improve", or shorten it. Changes to voice rules or lint checks go to the spec first.

### Cache TTL bug
The spec says `cache_control: {type: "ephemeral"}` but for Batch API this must be `{"type": "ephemeral", "ttl": "1h"}`. The 5-minute default expires mid-batch. Always use the 1-hour TTL.

### HubSpot property type
Email body properties (`email_1_body` through `email_5_body`) **must** be `fieldType: textarea`, not `fieldType: text`. The pipeline runs a preflight check before any write. Never skip this check.

### Teams webhook
Must be a **Power Automate** webhook URL. The legacy `webhook.office.com` format was retired 2026-03-31.

### No invented content
The lint checks enforce this, but the pipeline must **never** write output to HubSpot that failed lint after one regeneration attempt. Invalid output is always better than silent bad output.

### Batch API result types
Handle all 4 result types explicitly: `succeeded`, `errored` (two subtypes), `canceled`, `expired`. Do not treat non-succeeded results the same — `invalid_request_error` must not be retried.

## Tech Stack

- Python 3.12
- `anthropic==0.122.0` (Batch API: `client.messages.batches.*`)
- `hubspot-api-client==12.0.0` (not `hubspot-sdk` — that's an unstable alpha)
- `pydantic==2.13.4` (v2) — for LLM output validation at API boundary only
- `requests==2.32.3` — for Teams webhook
- GitHub Actions (two-job: `prepare` → `complete`)
- GitHub Secrets: `HUBSPOT_TOKEN`, `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL`

## Package Structure

```
pipeline/
  __init__.py
  models.py          # ContactBrief dataclass, GeneratedOutput Pydantic model
  hubspot_client.py  # HubSpot API client with rate limiter
  rate_limiter.py    # Proactive rate limiter (100 req/10s; 4 req/s search)
  stages/
    assemble.py      # Stage 1
    filter.py        # Stage 2
    route.py         # Stage 3
    generate.py      # Stage 4
    lint.py          # Stage 5
    assemble_bodies.py  # Stage 6
    write_back.py    # Stage 7
run.py               # Entry point / orchestrator
.github/workflows/
  pipeline.yml       # Two-job workflow (prepare + complete)
```
