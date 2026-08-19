# Technology Stack

**Project:** Staff Domain Re-Engagement Pipeline
**Researched:** 2026-08-19
**Overall confidence:** HIGH (all critical decisions verified against official docs or Context7)

---

## Recommended Stack

### Core Runtime

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| Python | 3.12 | Pipeline runtime | LTS, supported by all libraries below; 3.13 available but ecosystem adoption lags |
| GitHub Actions | N/A | Execution host | Required by project constraints; `workflow_dispatch` with up to 25 inputs (limit raised Dec 2025) |

### Anthropic / LLM

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `anthropic` (Python SDK) | 0.122.0 | Batch API + realtime calls | Official SDK, latest as of 2026-08-13; ships typed `MessageBatch`, `MessageBatchIndividualResponse`, and `JSONLDecoder`; use `client.messages.batches.create/retrieve/results` directly — no wrapper needed |

### HubSpot

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `hubspot-api-client` | 12.0.0 | HubSpot CRM reads and writes | Stable, production-ready, actively maintained by HubSpot, supports Python 3.7+, covers contacts/companies/deals/notes/engagements/tasks via `CRM v3` and legacy engagements v1. DO NOT use `hubspot-sdk` (version 0.1.0a9 as of May 2026 — pre-release alpha, not suitable for production). |

### Validation

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `pydantic` | 2.13.4 | Validate LLM JSON output (8-key schema e1–e5, call1, call2, pin) | Fastest option; `model_validate_json(raw_string)` parses and validates in one call; rich error messages surfacing exactly which key failed; v2 is Rust-backed. Dataclasses and TypedDict do not validate at runtime — they only annotate. |

### HTTP / Notifications

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `requests` | 2.32.x | Microsoft Teams webhook POST | One HTTP call to a webhook URL; no async needed; already a transitive dependency of `hubspot-api-client`. Using `httpx` for this alone adds unnecessary complexity. |
| `urllib3` | (transitive) | — | Pulled in by `requests`; no direct dependency needed |

### Dev / Testing

| Technology | Version | Purpose | Why |
|------------|---------|---------|-----|
| `pytest` | 8.x | Unit tests | Standard; supports `--dry-run`-style fixtures |
| `python-dotenv` | 1.x | Local secret loading during dev | Reads `.env` into `os.environ` so dev loop mirrors CI secret injection; never committed |

---

## Answers to Specific Questions

### Q1: HubSpot HTTP client — SDK vs httpx vs requests

**Use `hubspot-api-client` 12.0.0.**

- The official `hubspot-sdk` package is a pre-release alpha (0.1.0a9, May 2026). Do not use it in production.
- `hubspot-api-client` is the stable, HubSpot-maintained library and wraps all v3 endpoints you need (contacts, companies, deals, CRM notes) plus the legacy v1 engagements API for creating CALL tasks with associations.
- Raw `httpx` or `requests` means hand-writing every endpoint, auth header, retry, and pagination loop. At 500 contacts across 7 object types, that is significant boilerplate and a maintenance liability.
- **Exception:** the Teams webhook notification is one raw POST — use `requests` directly there; do not import the SDK for that.

### Q2: Passing contact IDs from HubSpot workflow to `workflow_dispatch`

**Pattern: single JSON-string input, parsed in Python.**

GitHub Actions `workflow_dispatch` inputs are all strings. The limit as of December 2025 is 25 inputs with a total payload cap of 65,535 characters. For 500 HubSpot contact IDs (each ~10 digits) the JSON array `["12345678","23456789",...]` is roughly 7,000 characters — well within the cap.

Recommended workflow YAML:

```yaml
on:
  workflow_dispatch:
    inputs:
      contact_ids:
        description: 'JSON array of HubSpot contact IDs'
        required: true
        type: string
      dry_run:
        description: 'Set to "true" to skip HubSpot writes'
        required: false
        default: 'false'
        type: string
      lane:
        description: 'Pipeline lane (e.g. lane_a)'
        required: false
        default: 'lane_a'
        type: string
```

In Python, receive and parse:

```python
import json, os
contact_ids: list[str] = json.loads(os.environ["INPUT_CONTACT_IDS"])
dry_run: bool = os.environ.get("INPUT_DRY_RUN", "false").lower() == "true"
```

GitHub passes `workflow_dispatch` inputs as env vars prefixed `INPUT_` when the workflow passes them to a step via `env:`. HubSpot's webhook call to the GitHub API dispatches against the `workflow_dispatch` trigger using its REST endpoint, passing `inputs` as a JSON object.

**Payload size for 500 contacts:** ~5–7 KB. Comfortably within the 65 KB limit.

### Q3: Anthropic Batch API — polling, partial failures, expiry

Source: official Anthropic docs (verified 2026-08-19).

**How it works:**

1. `client.messages.batches.create(requests=[...])` — submit up to 100,000 requests (or 256 MB) as one batch. Each request carries a `custom_id` (1–64 chars, `^[a-zA-Z0-9_-]{1,64}$`) for result matching.
2. Returns immediately with `processing_status: "in_progress"`.
3. Poll: `client.messages.batches.retrieve(batch_id)` every 60 seconds until `processing_status == "ended"`. Most batches finish within 1 hour; SLA is 24 hours.
4. Fetch results: `client.messages.batches.results(batch_id)` returns a `JSONLDecoder` iterator — stream it, do not buffer all 500 results in memory.

**Result types per request:**

| Type | Meaning | Action |
|------|---------|--------|
| `succeeded` | Normal result, message content available | Extract JSON from `result.message.content[0].text` |
| `errored` | `invalid_request_error` (bad params) or server error | `invalid_request_error` = fix and resubmit; server error = retry the individual contact |
| `expired` | Batch hit 24 h without completing this request | Resubmit individually |
| `canceled` | Batch was canceled before this request ran | N/A for this pipeline |

**Partial failure handling pattern:**

```python
succeeded, failed = [], []
for result in client.messages.batches.results(batch_id):
    if result.result.type == "succeeded":
        succeeded.append((result.custom_id, result.result.message))
    else:
        failed.append((result.custom_id, result.result.type))
# failed contacts go to the Teams review notification
```

Use `custom_id = contact_id` so mapping back to HubSpot records is O(1).

**GitHub Actions timeout constraint:** GitHub's hard maximum job timeout is 360 minutes (6 hours). Since the Batch API 99th-percentile processing time is well under 1 hour for 500 requests, the poll loop fits within a single job. Set `timeout-minutes: 180` as a safety margin.

**Cache note:** The official docs confirm that `cache_control: ephemeral` (1-hour TTL, announced as the long-duration cache option) is supported in batch requests — use it on the system prompt to reduce per-contact token costs.

### Q4: Pydantic vs dataclasses vs TypedDict for LLM output validation

**Use Pydantic v2 (`pydantic` 2.13.4).**

| Option | Runtime validation | Error messages | JSON parsing | Verdict |
|--------|-------------------|---------------|--------------|---------|
| `pydantic.BaseModel` | Yes (Rust core) | Rich, field-level | `model_validate_json(raw)` in one call | **Use this** |
| `dataclasses` | No | None at runtime | Manual `json.loads` + no type checking | Do not use |
| `TypedDict` | No | None at runtime | Manual `json.loads` + no type checking | Do not use |

The LLM returns a raw JSON string. Pydantic's `model_validate_json` parses and validates in one operation, raises `ValidationError` with field-level detail (e.g., "field `e3` missing"), and rejects unexpected keys if `model_config = ConfigDict(extra='forbid')`.

Recommended model for the 8-key output:

```python
from pydantic import BaseModel, ConfigDict

class GenerationOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    e1: str
    e2: str
    e3: str
    e4: str
    e5: str
    call1: str
    call2: str
    pin: str
```

Validation call: `GenerationOutput.model_validate_json(llm_raw_text)`. On `ValidationError`, regenerate once then flag.

### Q5: Single script vs multiple steps for the 7-stage pipeline

**Use a single Python entry-point script orchestrated by one GitHub Actions job with multiple named steps.**

Rationale:

- All 7 stages share the same in-memory state (assembled contacts, filtered list, generated output, lint results). Splitting into separate jobs means serializing/deserializing that state to files and artifact uploads between jobs — unnecessary complexity for a pipeline with no parallelism requirement between stages.
- Separate jobs would also require storing intermediate state as GitHub Artifacts, adding latency and complicating error recovery.
- The correct boundary is: **one GitHub Actions job** (`run_pipeline`) with named steps (`Assemble`, `Filter`, `Route`, `Generate`, `Lint`, `Finalize`, `Write-Back`, `Notify`). Each step calls the Python script with a `--stage` flag, OR (simpler) one step runs the whole pipeline end-to-end with structured internal logging.
- **Recommended structure:** one `python pipeline.py --contact-ids "$INPUT_CONTACT_IDS" --lane "$INPUT_LANE" --dry-run "$INPUT_DRY_RUN"` invocation. Internal stage functions are importable modules (`stages/assemble.py`, `stages/filter.py`, etc.) for testability.
- Multiple lane support: `lane_a/config.py` contains the routing table, system prompt reference, and lint rules. The pipeline entry point loads the lane config by name. When Lane B arrives, add `lane_b/config.py` — zero changes to core pipeline.

GitHub Actions workflow structure:

```yaml
jobs:
  run_pipeline:
    runs-on: ubuntu-latest
    timeout-minutes: 180
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      HUBSPOT_ACCESS_TOKEN: ${{ secrets.HUBSPOT_ACCESS_TOKEN }}
      TEAMS_WEBHOOK_URL: ${{ secrets.TEAMS_WEBHOOK_URL }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt
      - name: Run pipeline
        run: |
          python pipeline.py \
            --contact-ids '${{ inputs.contact_ids }}' \
            --lane '${{ inputs.lane }}' \
            --dry-run '${{ inputs.dry_run }}'
```

### Q6: GitHub Secrets for HubSpot token and Anthropic API key

**Standard GitHub repository secrets, injected as environment variables.**

- Store as repo-level secrets: `ANTHROPIC_API_KEY`, `HUBSPOT_ACCESS_TOKEN`, `TEAMS_WEBHOOK_URL`.
- Reference in workflow YAML via `${{ secrets.NAME }}`, mapped to step-level `env:` block (see Q5 example above).
- In Python: read via `os.environ["ANTHROPIC_API_KEY"]` — raise `EnvironmentError` early if missing to fail fast.
- Never print secrets in logs. Never hardcode in source. Never use GitHub Variables (plain-text) for tokens.
- The `anthropic` SDK auto-reads `ANTHROPIC_API_KEY` from the environment if you instantiate `anthropic.Anthropic()` with no arguments — no manual passing required.
- The `hubspot-api-client` requires you to pass the token explicitly: `hubspot.Client.create(access_token=os.environ["HUBSPOT_ACCESS_TOKEN"])`.

---

## Alternatives Considered

| Category | Recommended | Alternative | Why Not |
|----------|-------------|-------------|---------|
| HubSpot client | `hubspot-api-client` 12.0.0 | `hubspot-sdk` 0.1.0a9 | Pre-release alpha; explicitly flagged as "may not be stable for production" on PyPI |
| HubSpot client | `hubspot-api-client` | Raw `httpx` | Re-implementing pagination, auth, and all endpoint paths is ~weeks of work for no benefit |
| Validation | `pydantic` v2 | `dataclasses` / `TypedDict` | Neither validates at runtime; would require manual JSON schema checking |
| Validation | `pydantic` v2 | `jsonschema` library | More verbose; less Pythonic; no IDE integration |
| Teams notification | `requests` | `httpx` | Single synchronous POST; async client overhead unnecessary |
| Teams notification | `requests` | GitHub Marketplace Teams actions | Pipeline already in Python; adding a separate Action step for one POST is unnecessary indirection |
| Pipeline structure | Single job + single script | Multi-job pipeline | Shared in-memory state; no parallelism requirement; artifact passing overhead |

---

## Installation

```bash
# requirements.txt
anthropic==0.122.0
hubspot-api-client==12.0.0
pydantic==2.13.4
requests==2.32.3
python-dotenv==1.0.1   # dev only — remove from production requirements or guard with extras

# dev/test
pytest==8.3.x
```

```bash
pip install anthropic==0.122.0 hubspot-api-client==12.0.0 pydantic==2.13.4 requests==2.32.3
```

---

## Key Pitfall: Teams Webhook URL Type (2026)

Office 365 Connectors (webhook.office.com URLs) were retired on 2026-03-31. Any existing Teams webhook URL using the `webhook.office.com` domain will no longer work. The replacement is a Power Automate workflow URL. When setting up the `TEAMS_WEBHOOK_URL` secret, use the Power Automate-generated URL, not a legacy connector URL.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Anthropic SDK + Batch API | HIGH | Verified against official Anthropic docs (platform.claude.com) + Context7, 2026-08-19 |
| `anthropic` package version | HIGH | Verified on PyPI: 0.122.0, released 2026-08-13 |
| `hubspot-api-client` version | HIGH | Verified on PyPI: 12.0.0, released 2025-05-07 |
| `hubspot-sdk` pre-release status | HIGH | Verified on PyPI: 0.1.0a9, flagged as pre-release |
| Pydantic v2 version | HIGH | Verified on PyPI: 2.13.4, released 2026-05-06 |
| `workflow_dispatch` input limits | HIGH | GitHub Changelog: 25 inputs, 65,535 char limit (Dec 2025) |
| GitHub Actions max timeout | HIGH | Documented: 360 minutes hard limit |
| Batch API 24h SLA + result types | HIGH | Official Anthropic docs verified 2026-08-19 |
| Teams webhook migration | MEDIUM | Multiple sources confirm Office 365 Connectors retired 2026-03-31; Power Automate replacement confirmed |
| Single-job vs multi-job pattern | MEDIUM | Based on GitHub Actions best practices + architectural reasoning; no single authoritative source |

---

## Sources

- Anthropic Batch Processing Docs: https://platform.claude.com/docs/en/build-with-claude/batch-processing (verified 2026-08-19)
- Anthropic Python SDK (Context7): /anthropics/anthropic-sdk-python
- `anthropic` on PyPI: https://pypi.org/project/anthropic/ — version 0.122.0, 2026-08-13
- `hubspot-api-client` on PyPI: https://pypi.org/project/hubspot-api-client/ — version 12.0.0, 2025-05-07
- `hubspot-sdk` on PyPI: https://pypi.org/project/hubspot-sdk/ — version 0.1.0a9 pre-release
- HubSpot API Python GitHub: https://github.com/HubSpot/hubspot-api-python
- Pydantic on PyPI: https://pypi.org/project/pydantic/ — version 2.13.4, 2026-05-06
- Pydantic v2 docs (Context7): /pydantic/pydantic
- GitHub Actions workflow_dispatch 25 inputs: https://github.blog/changelog/2025-12-04-actions-workflow-dispatch-workflows-now-support-25-inputs/
- GitHub Actions payload size discussion: https://github.com/orgs/community/discussions/120093
- Teams webhook Office 365 retirement: multiple sources confirming 2026-03-31 retirement
