# Architecture Patterns

**Domain:** Multi-lane AI generation pipeline (GitHub Actions + HubSpot + Anthropic)
**Project:** Staff Domain Re-Engagement Pipeline
**Researched:** 2026-08-19
**Overall confidence:** HIGH (all key claims verified against official docs or official community threads)

---

## Recommended Architecture

### High-Level Shape

Two GitHub Actions jobs in one workflow. The first job runs stages 1-3 (assemble, filter,
route), submits the Anthropic Batch, and writes the batch ID to a job output. The second job
depends on the first, polls for batch completion, then runs stages 5-7 (lint, assemble bodies,
write-back) and sends the Teams notification. Dry-run mode skips write-back in job 2 only.

```
workflow_dispatch (contact_ids JSON array)
        |
        v
  [Job 1: prepare]           timeout-minutes: 20
    Stage 1 — Assemble       HubSpot reads (5 API calls per contact)
    Stage 2 — Filter         Exclusion logic, build exclusion report
    Stage 3 — Route          Vertical lookup, assign closes
    Submit Batch             POST /v1/messages/batches
    Output batch_id -------> job output / artifact JSON
        |
        v
  [Job 2: complete]          timeout-minutes: 90  (batch SLA: <1 hr typical)
    Poll batch_id            GET /v1/messages/batches/{id} every 60s
    Stage 5 — Lint           18 checks; regenerate hard failures once (realtime call)
    Stage 6 — Assemble       Append placeholders
    Stage 7 — Write-back     HubSpot batch PATCH (100/req), then create engagements
    Teams webhook            Exclusion report + sampled/flagged contacts
```

The workflow total fits inside GitHub Actions' 6-hour hard limit with room to spare.
Splitting into two jobs is NOT required by GitHub's timeout — a single job running 90 minutes
is fine. The split is recommended because it isolates the polling loop as an explicit
responsibility boundary and makes re-running the expensive "complete" job independently
possible if write-back fails partway through.

---

## Component Boundaries

### Directory / File Structure

Use a package structure (not a single file). At 7 stages, multiple data models, lane configs,
and HubSpot + Anthropic clients, a flat single file becomes unnavigable. The package is still
small (8-10 files) but lets each stage live in its own module.

```
pipeline/
  __init__.py
  models.py          ContactBrief dataclass + PipelineResult dataclass
  lanes/
    __init__.py
    base.py          LaneConfig dataclass (system prompt path, output schema, close logic)
    lane_a.py        Lane A LaneConfig instance + brief assembly overrides
  stages/
    assemble.py      Stage 1 — HubSpot reads + brief construction
    filter.py        Stage 2 — Exclusion logic
    route.py         Stage 3 — Vertical routing table
    generate.py      Stage 4 — Anthropic Batch API submission + polling
    lint.py          Stage 5 — 18 checks, regeneration on hard failures
    assemble_bodies.py  Stage 6 — Link/placeholder appending
    write_back.py    Stage 7 — HubSpot writes
  hubspot/
    client.py        Thin wrapper: contacts, companies, deals, notes, engagements, owners
  notifications.py   Teams webhook formatter
  run.py             Entrypoint: arg parsing, orchestrates Job 1 and Job 2 flow
run_pipeline.yml     GitHub Actions workflow
```

### What Talks to What

| Component | Reads From | Writes To |
|-----------|-----------|-----------|
| `run.py` | workflow_dispatch payload | job outputs (batch_id), exit code |
| `stages/assemble.py` | HubSpot API (via `hubspot/client.py`) | `ContactBrief` instances |
| `stages/filter.py` | `ContactBrief` list | filtered list + `ExclusionRecord` list |
| `stages/route.py` | `ContactBrief.industry` | `ContactBrief.routing` (mutates in place) |
| `stages/generate.py` | `ContactBrief` list + `LaneConfig` | Batch ID (submit) / `GeneratedOutput` dict (poll) |
| `stages/lint.py` | `GeneratedOutput` per contact | `LintResult` per contact; may call generate.py once for regen |
| `stages/assemble_bodies.py` | `LintResult.bodies` + `ContactBrief.routing` | `FinalBodies` per contact |
| `stages/write_back.py` | `FinalBodies` list | HubSpot API (batch PATCH + engagement creates) |
| `notifications.py` | `ExclusionRecord` list + `LintResult` list | Teams webhook POST |
| `lanes/lane_a.py` | `ContactBrief` fields | `LaneConfig` (injected into generate stage) |

No stage imports another stage. `run.py` is the only orchestrator. This means stages are
independently testable with fixtures.

---

## Data Flow

### The ContactBrief — Central Data Carrier

Use a **dataclass** (stdlib, no dependency) for `ContactBrief`. It flows through all 7 stages.
Pydantic adds runtime validation cost on every construction; since the data is already typed
and trusted by the time stages receive it, a dataclass is the right tool. Use Pydantic only at
the HubSpot API boundary to parse/validate raw JSON responses into typed objects — that is the
untrusted edge where validation earns its overhead.

```python
@dataclass
class ContactBrief:
    # Identity
    contact_id: str
    firstname: str
    lastname: str
    jobtitle: str
    company_id: str
    company_name: str
    industry: str
    country: str

    # Engagement history (resolved in Stage 1)
    handover_name: str           # first name of last-activity owner
    handover_date: str
    handover_channel: str        # "call" | "email"
    last_activity_owner_active: bool
    current_owner_id: str
    current_owner_firstname: str

    # Colleagues (Stage 1)
    colleagues: list[ColleagueSummary]  # only contacted ones
    sole_contact: bool

    # History (Stage 1)
    deal_summary: str            # formatted text block or NO_DEAL sentinel
    has_deal: bool
    notes_bullets: list[str]
    internal_flags: list[str]    # INTERNAL - NEVER REFERENCE items
    live_hiring_signals: list[str]

    # Routing (Stage 3, populated after filter pass)
    case_study_name: str | None = None
    email3_url: str | None = None
    email4_url: str | None = None
    assigned_close: int | None = None   # 1-5

    # Pipeline metadata
    lane: str = "A"
    exclusion_reason: str | None = None   # set by Stage 2 if excluded
    touch_count: int = 0                  # total num_contacted_notes across all contacts
```

`ContactBrief` is immutable-by-convention after Stage 3. Stages 4-7 produce new output
objects rather than mutating the brief.

### Stage Output Objects

```python
@dataclass
class GeneratedOutput:
    contact_id: str
    emails: dict[str, EmailDraft]   # keys: e1-e5, each has subject + body
    batch_request_id: str           # Anthropic custom_id
    regenerated: bool = False

@dataclass
class LintResult:
    contact_id: str
    passed: bool
    hard_failures: list[str]
    soft_warnings: list[str]
    final_emails: dict[str, EmailDraft] | None  # None if hard-failed after regen
    flagged_for_review: bool

@dataclass
class FinalBodies:
    contact_id: str
    emails: dict[str, EmailDraft]   # with placeholders appended
```

### Data Flow Direction

```
workflow_dispatch payload
  -> [str] contact_ids
  -> [list[ContactBrief]]          Stage 1 (assemble)
  -> [list[ContactBrief]], [list[ExclusionRecord]]   Stage 2 (filter)
  -> [list[ContactBrief]] (routing fields populated)  Stage 3 (route)
  -> batch_id (string) written to artifact            Stage 4 submit (end of Job 1)

  -- Job 2 starts, reads batch_id from artifact --

  -> batch poll: batch_id -> [list[GeneratedOutput]]  Stage 4 poll
  -> [list[LintResult]]            Stage 5 (lint)
  -> [list[FinalBodies]]           Stage 6 (assemble_bodies)
  -> HubSpot write (no return)     Stage 7 (write_back)
  -> Teams POST (no return)        Notifications
```

---

## Patterns to Follow

### Pattern 1: Two-Job Workflow with Artifact Handoff

The Anthropic Batch API documents that most batches complete in under 1 hour but can take up
to 24 hours. Holding a live GitHub Actions runner open for that window is wasteful and
fragile. The correct pattern:

- Job 1 writes `batch_id` and the assembled `ContactBrief` list (as JSON artifact) to
  GitHub Actions artifact storage (`actions/upload-artifact`).
- Job 2 uses `needs: prepare`, downloads the artifact, then polls.
- The polling loop in Job 2 sleeps 60 seconds between checks. At 500 contacts, even a
  10-minute batch completion means ~10 poll iterations — well within `timeout-minutes: 90`.
- If the batch does NOT complete within Job 2's timeout, the run fails with a clear error
  and the batch_id is preserved in the artifact for manual retry inspection.

Note from official Batch API docs: `cache_control: ephemeral` prompt caching is supported
inside Batch requests. Use the 1-hour cache duration (not ephemeral) when submitting batches,
since ephemeral cache entries written during batch submission are likely to expire before the
batch processes. Prefer `cache_control: {"type": "ephemeral"}` on the system prompt only for
realtime (dev) calls.

### Pattern 2: Lane Abstraction via LaneConfig Dataclass

```python
@dataclass
class LaneConfig:
    name: str
    system_prompt: str               # verbatim text, not a path
    output_schema: dict              # JSON schema the model must conform to
    brief_assembler: Callable[[ContactBriefRaw], ContactBrief]
    close_assignment: Callable[[ContactBrief], int]   # returns 1-5

# lanes/lane_a.py
LANE_A = LaneConfig(
    name="A",
    system_prompt=LANE_A_SYSTEM_PROMPT,
    output_schema=LANE_A_SCHEMA,
    brief_assembler=assemble_lane_a_brief,
    close_assignment=assign_close_lane_a,
)
```

The generate stage receives a `LaneConfig` object. Adding Lane B means creating
`lanes/lane_b.py` with its own `LaneConfig` instance. Nothing in the shared stages changes.
The `run.py` entrypoint selects the config from the workflow_dispatch `lane` parameter
(default: `"A"`).

### Pattern 3: HubSpot Write-Back Order (Note Before Pin)

The pin requires `hs_pinned_engagement_id` to be set on the contact object. To pin a note:

1. Create the note engagement via `POST /crm/v3/objects/notes` (with body, timestamp,
   associations to contact and owner).
2. Capture the returned note `id`.
3. Include `"hs_pinned_engagement_id": note_id` in the batch PATCH for the contact properties
   (the same request that writes `email_1_subject` through `email_5_body`).

This collapses write steps: the 10 email properties and the pin happen in one PATCH per
contact. The two call task engagements are created separately since they cannot be batched
with the PATCH endpoint. Execution order within Stage 7:

```
1. Create note → capture note_id         (per contact, individual POST)
2. Create call task 1 + call task 2      (per contact, individual POSTs)
3. Batch PATCH contacts (100 per batch): 10 email props + hs_pinned_engagement_id
```

If note creation fails for a contact, skip the pin property in that contact's PATCH and log a
soft failure — do not abort the whole run.

### Pattern 4: Continue-and-Collect Error Handling

Do NOT fail fast across contacts. The pipeline processes independent records; one bad contact
must not block 499 others. The pattern:

```python
results: list[PipelineResult] = []
for brief in passing_briefs:
    try:
        result = process_contact(brief, lane_config)
        results.append(result)
    except ContactPipelineError as e:
        results.append(PipelineResult(contact_id=brief.contact_id, error=str(e)))
```

`PipelineResult` carries either a `FinalBodies` or an error string. The write-back stage
skips errored contacts. The Teams notification includes a failed-contact count and list.
This is consistent with how Stage 2 exclusions work: records that fail go to a report, not
the bin.

The one exception: if Stage 1 (assemble) raises an unrecoverable error for ALL contacts
(e.g., HubSpot auth failure), fail fast at the run level with a non-zero exit code and a
clear error message. Don't attempt Stage 2 with an empty list.

### Pattern 5: Dry-Run via Flag, Not Separate Code Path

Pass `--dry-run` as a workflow_dispatch input. In `run.py`, set `DRY_RUN = True`. The only
place this flag is read is in `write_back.py` — it logs what would be written and returns
early. All stages before write-back execute normally. This gives confidence that lint and
assembly are sound before committing any real HubSpot writes.

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Single Long-Polling Job

**What:** One job that submits the batch then polls indefinitely until done.
**Why bad:** GitHub Actions has a 6-hour job limit, but a stalled or slow batch could
approach that. More practically, if the polling job is killed, you lose the batch_id and
cannot recover without checking the Anthropic console manually.
**Instead:** Two-job split with artifact-stored batch_id, as described above.

### Anti-Pattern 2: Using `cache_control: ephemeral` Inside Batch Requests

**What:** Adding ephemeral prompt cache to the system prompt in every batched request.
**Why bad:** Official Anthropic docs explicitly warn: ephemeral cache entries written during
batch processing "would likely expire before the follow-up request runs." For batches, use
the 1-hour cache duration instead, or accept no caching inside the batch (the 50% batch
discount is the primary cost control).
**Instead:** Use `cache_control: {"type": "ephemeral"}` for realtime dev calls only.

### Anti-Pattern 3: Dict as the Primary Data Carrier

**What:** Passing plain `dict` objects between stages.
**Why bad:** No IDE autocomplete, no static analysis, silent KeyErrors at runtime. At 7
stages and 10+ fields, dict-of-dicts becomes unreadable.
**Instead:** `ContactBrief` dataclass as above. Dicts are fine only for the raw HubSpot
API response before it is parsed into the dataclass.

### Anti-Pattern 4: Calling HubSpot Write APIs Inside the Batch Polling Loop

**What:** Writing partial results to HubSpot as each batch request completes.
**Why bad:** The Batch API delivers results only after ALL requests in the batch are done
(or the 24-hour expiry). There is no per-request streaming of results during processing.
**Instead:** All results arrive at once when batch status is `ended`. Process in bulk.

### Anti-Pattern 5: Putting Lane Logic in the Shared Stage Functions

**What:** `if lane == "A": ... elif lane == "B": ...` inside `generate.py` or `lint.py`.
**Why bad:** Every new lane contaminates every stage. Tests require mocking lane branches.
**Instead:** Lane-specific logic lives only in `lanes/lane_a.py` and is injected via
`LaneConfig`. Shared stages are lane-agnostic.

---

## Suggested Build Order

Dependencies drive this order. Each phase produces something the next phase can test against.

### Phase 1 — Data Foundation

Build `models.py` and `hubspot/client.py` first. Nothing else can be tested without them.

- `ContactBrief` dataclass (even with placeholder fields)
- HubSpot client: `get_contact`, `get_company`, `get_all_contacts_for_company`,
  `get_deals_for_company`, `get_notes_for_contact`, `get_engagements_for_contact`,
  `get_owner`
- Smoke test against the HubSpot sandbox with one known contact ID

### Phase 2 — Stage 1: Assemble

The most complex stage. Build and validate before touching generation.

- `stages/assemble.py`: all 6 section builders (colleagues, deals, notes, handover,
  geography, close assignment)
- Bot-noise filter for notes
- Close bank assignment logic
- Produce readable brief for 3-5 real Lane A contacts; have JP review output

### Phase 3 — Stages 2 + 3: Filter + Route

Deterministic logic, easy to unit test.

- `stages/filter.py`: E1-E6 exclusion checks
- `stages/route.py`: vertical lookup table (substring, case-insensitive, first-hit)
- Exclusion report formatter

### Phase 4 — Stage 4: Generate (Lane A, realtime first)

Build against realtime API before switching to Batch API.

- `lanes/lane_a.py`: `LaneConfig` with system prompt verbatim from spec §5.2
- `stages/generate.py`: realtime path first (one `client.messages.create` per contact)
- Validate output schema (keys e1-e5, subject + body)
- Batch API path second (batch submit + polling loop + artifact write)

### Phase 5 — Stage 5: Lint

Build after first real outputs exist to test against.

- All 18 hard failure checks
- All 5 soft warning checks
- Regeneration path (one realtime call on hard failure; mark as flagged if fails again)
- Cross-contact variety check (same-company duplicate subject detection)

### Phase 6 — Stage 6 + 7: Assemble Bodies + Write-Back

Final integration, test against HubSpot sandbox before prod.

- `stages/assemble_bodies.py`: placeholder appending per spec §6
- Pre-send `[` guard
- `stages/write_back.py`: note create -> call task creates -> batch PATCH contacts
- Verify MULTI-LINE TEXT rendering in HubSpot UI on 2-3 records before full run
- Dry-run mode end-to-end test

### Phase 7 — GitHub Actions Wiring + Teams Notification

Last because it wraps the entire working pipeline.

- `notifications.py`: exclusion report + sampled contacts formatter
- Teams webhook POST
- `run_pipeline.yml`: two-job workflow, secrets, dry_run input, contact_ids input
- End-to-end pilot run: 20 contacts, dry-run first, then live with JP reviewing before
  confirming write-back

---

## Scalability Considerations

| Concern | At 50 contacts (pilot) | At 500 contacts (full run) | At 5000+ contacts |
|---------|------------------------|----------------------------|-------------------|
| Anthropic cost | Use realtime API | Batch API (50% discount, ~$2-3) | Batch API, same |
| HubSpot read API calls | ~250 (5 per contact) | ~2500 | Consider caching company data |
| HubSpot batch PATCH | 1 batch of 50 | 5 batches of 100 | 50 batches |
| Batch poll wait | ~5 min typical | ~15-30 min typical | ~1 hr typical |
| GitHub Actions cost | Negligible | Negligible | Still negligible at hosted runners |
| Note pinning | One POST per contact | One POST per contact | Same (no batch endpoint for note creation) |

The architecture does not need to change below 5000 contacts. Above that, HubSpot reads
become the bottleneck and caching company-level data (deals, all associated contacts) across
same-company contacts becomes worth implementing.

---

## Key Constraints Affecting Architecture

1. **Batch API does NOT stream results per request.** All results arrive when the batch
   status transitions to `ended`. Design the pipeline around bulk result collection, not
   incremental.

2. **Note pinning requires a two-step write.** Create note first, capture ID, then include
   `hs_pinned_engagement_id` in the contact PATCH. This is confirmed via the HubSpot
   community: the CRM object API (not the legacy engagements API) sets the pinned property.
   The spec flags this as unconfirmed — it IS confirmed via official community threads.
   Verify in sandbox before pilot.

3. **HubSpot batch update limit is 100 records per request.** At 500 contacts, that is 5
   PATCH requests. Well within rate limits (100 requests / 10 seconds on private apps).

4. **System prompt in Batch requests: use 1-hour cache, not ephemeral.** The Anthropic docs
   explicitly warn that ephemeral cache set during batch submission expires before the batch
   processes. For 500 contacts sharing one system prompt, the cache hit savings are real but
   require the longer-duration cache type.

5. **GitHub Actions 6-hour hard limit is not a binding constraint here.** A 90-minute
   timeout on Job 2 gives ample headroom for the Batch API's sub-1-hour typical SLA while
   providing a clean failure mode if the batch stalls.

---

## Sources

- Anthropic Batch Processing official docs: https://platform.claude.com/docs/en/build-with-claude/batch-processing
- HubSpot pin note via API (community confirmed): https://community.hubspot.com/t5/APIs-Integrations/Pin-a-note-with-engagement-API/m-p/413062
- HubSpot batch update contacts endpoint: https://api.hubapi.com/crm/v3/objects/contacts/batch/update
- HubSpot API rate limits (production): https://www.scopiousdigital.com/blog/hubspot-api-rate-limits-production
- GitHub Actions timeout reference: https://docs.github.com/en/actions/reference/actions-limits
- GitHub Actions artifact passing between jobs: https://docs.github.com/en/enterprise-server@2.22/actions/configuring-and-managing-workflows/persisting-workflow-data-using-artifacts
- Dataclasses vs Pydantic lifecycle guidance: https://medium.com/@ThinkingLoop/dataclasses-vs-pydantic-decide-by-lifecycle-99160d2b3403
