# Phase 4: Generation — Research

**Researched:** 2026-08-27
**Domain:** Anthropic Python SDK — messages.create, prompt caching, Message Batches API
**Confidence:** HIGH (all key claims verified against installed SDK 0.122.0 and Context7)

---

## Summary

Phase 4 builds `scripts/generate_campaign.py`. It reads each `brief_{id}.json` produced by Phase 3, constructs the user message from `brief_text`, calls `claude-sonnet-5` with the cached system prompt, parses an 8-key JSON response, and writes `generated_{id}.json`. The script runs in either realtime mode (default, sequential per-contact loop) or Batch API mode (`INPUT_USE_BATCH_API=true`, submit-then-poll-then-retrieve).

The three technically novel aspects of this phase compared to prior phases are: (1) passing the system prompt as a `list[TextBlockParam]` with `cache_control: {"type": "ephemeral"}` rather than as a plain string; (2) wiring the Anthropic Message Batches API behind an env-var toggle; and (3) parsing a specific 8-key JSON output schema with well-defined error behaviour.

All SDK patterns have been verified against the installed `anthropic==0.122.0` library. The system prompt is confirmed to contain an 8-key OUTPUT schema (e1–e5 with `subject`+`body`, call1/call2/pin with `body` only). The `brief_{id}.json` schema from Phase 3 carries `brief_text`, `case_study`, `email3_url`, `email4_url`, `close_option`, and `close_text` — the user message is entirely `brief_text` (the URLs and case study name are already embedded in it by `build_brief()`).

**Primary recommendation:** Keep the realtime path the primary loop (one `@retry(**ANTHROPIC_RETRY_KWARGS)` call per contact, per the established utils.py pattern). Add the Batch API path as a clearly-separated code branch behind the toggle, with its own submit + poll + retrieve + per-result-dispatch logic.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| GEN-01 | `scripts/generate_campaign.py` reads per-contact JSON and calls `claude-sonnet-5` | Loop structure reads `contact_ids.json`, loads each `brief_{id}.json`, calls API |
| GEN-02 | System prompt sent with `cache_control: {type: "ephemeral"}`; user message is the assembled brief | `system` param accepts `list[TextBlockParam]`; set `cache_control={"type": "ephemeral"}` on the single block |
| GEN-03 | `max_tokens=3000`; one call per contact; generates all 8 deliverables in one response | `max_tokens=3000` passed to `messages.create`; one call per brief |
| GEN-04 | Realtime API for iteration/pilot; Batch API wired for full-list runs via `INPUT_USE_BATCH_API` | Two code paths; `client.messages.batches.create()` + poll on `processing_status == "ended"` + `client.messages.batches.results()` |
| GEN-05 | Output parsed against 8-key schema: e1–e5 (subject+body) + call1/call2/pin (body only) | `json.loads()` on response text content; validate presence of all 8 keys |
| GEN-06 | On `stop_reason == "max_tokens"` raise clear error; on JSON parse failure save raw response before raising | `if response.stop_reason == "max_tokens": raise MaxTokensError(...)`; `except json.JSONDecodeError` writes raw file then re-raises |
| GEN-07 | Write result to `$RUNNER_TEMP/generated_{id}.json` | `json.dump(payload, f, indent=2)` after successful parse |
</phase_requirements>

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Brief loading | Script / filesystem | — | Reads RUNNER_TEMP files written by Phase 3 |
| API call (realtime) | Script → Anthropic API | — | Single synchronous call per contact via SDK |
| API call (batch) | Script → Anthropic Batch API | Script (poll loop) | Submit all contacts, poll status, retrieve JSONL |
| Response parsing | Script | — | `json.loads` on `response.content[0].text` |
| Error classification | Script | — | `stop_reason` check + JSON parse exception handling |
| Output write | Script / filesystem | — | Writes `generated_{id}.json` to RUNNER_TEMP |
| DLQ / retry | Script (`utils.py`) | — | Reuse `write_dlq` and `ANTHROPIC_RETRY_KWARGS` |

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| anthropic | 0.122.0 (installed) `>=0.30.0` (pinned) | Anthropic Python SDK — messages.create + batches | Official SDK; already in requirements.txt |
| tenacity | `>=9.0.0` | Retry decorator for API calls | Already in requirements.txt; `ANTHROPIC_RETRY_KWARGS` already in utils.py |
| json (stdlib) | stdlib | Parse model JSON output; load/write files | No external dep needed |
| os / sys (stdlib) | stdlib | Env vars, exit codes | Consistent with all prior scripts |
| time (stdlib) | stdlib | `time.sleep()` in batch polling loop | Consistent with batch polling patterns across the ecosystem |

[VERIFIED: npm registry / installed `anthropic==0.122.0` confirmed via `python -c "import anthropic; print(anthropic.__version__)"`]

**No new dependencies.** Phase 4 requires nothing beyond what is already in `requirements.txt`.

---

## Architecture Patterns

### System Architecture Diagram

```
contact_ids.json
       |
       v
  [generate_campaign.py main()]
       |
       +--[realtime mode]---> for each contact_id:
       |                          load brief_{id}.json
       |                          build_system_message()  → list[TextBlockParam] w/ cache_control
       |                          call_claude()            → @retry(ANTHROPIC_RETRY_KWARGS)
       |                          check stop_reason        → MaxTokensError if "max_tokens"
       |                          parse_output()           → json.loads on content[0].text
       |                          validate_schema()         → KeyError / ValueError if malformed
       |                          write generated_{id}.json
       |
       +--[batch mode]-------> build_requests()           → list of {custom_id, params}
                               client.messages.batches.create(requests=[...])
                               poll_until_ended()          → retrieve() loop + time.sleep(60)
                               client.messages.batches.results(batch_id)
                               for each MessageBatchIndividualResponse:
                                   if result.type == "succeeded":
                                       parse_output() → write generated_{id}.json
                                   else:
                                       write_dlq(contact_id, ..., "batch_api_error", ...)
```

### Recommended Project Structure

No new directories. One new file:

```
scripts/
├── generate_campaign.py    # Phase 4 — this file
├── fetch_record.py         # Phase 2 — already exists
├── exclude_and_route.py    # Phase 3 — already exists
└── utils.py                # shared helpers — zero-divergence policy
```

---

### Pattern 1: System prompt with cache_control (GEN-02)

The `system` parameter of `messages.create` accepts either a plain `str` or a `list[TextBlockParam]`. To cache the system prompt, pass it as a list with a single text block that carries `cache_control`.

[VERIFIED: Context7 /anthropics/anthropic-sdk-python — TextBlockParam type definition, CacheControlEphemeralParam type definition]

```python
# Source: anthropic SDK types/text_block_param.py + types/cache_control_ephemeral_param.py
from config.system_prompt import get_system_prompt

SYSTEM_MESSAGE = [
    {
        "type": "text",
        "text": get_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]
```

This is a module-level constant (computed once at import). Every call passes the same object; the first call writes the cache, subsequent calls read it at 10% price.

**Important:** `cache_control` on `TextBlockParam` marks a cache breakpoint at that block. The system prompt (~1500 tokens) exceeds the Anthropic minimum for caching (1024 tokens for claude-sonnet models). [ASSUMED — minimum token threshold from training knowledge; not re-verified in this session. Risk if wrong: cache silently does not activate, cost is higher but correctness is unaffected.]

---

### Pattern 2: Realtime call with retry (GEN-01, GEN-03)

[VERIFIED: Context7 /anthropics/anthropic-sdk-python — messages.create signature; utils.py ANTHROPIC_RETRY_KWARGS confirmed by reading the file]

```python
# Source: existing utils.py pattern + anthropic SDK messages.create
from tenacity import retry
from utils import ANTHROPIC_RETRY_KWARGS, write_dlq

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

@retry(**ANTHROPIC_RETRY_KWARGS)
def _call_realtime(brief_text: str) -> anthropic.types.Message:
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=3000,
        system=SYSTEM_MESSAGE,          # list[TextBlockParam] with cache_control
        messages=[{"role": "user", "content": brief_text}],
    )
```

`ANTHROPIC_RETRY_KWARGS` retries on 429 and 5xx up to 6 attempts with 60s max, honouring `Retry-After`. It is already defined in `utils.py` and must not be modified (zero-divergence policy).

---

### Pattern 3: Output parsing with named error classes (GEN-05, GEN-06)

The system prompt instructs the model to return exactly:

```json
{"e1":{"subject":"...","body":"..."},
 "e2":{"subject":"...","body":"..."},
 "e3":{"subject":"...","body":"..."},
 "e4":{"subject":"...","body":"..."},
 "e5":{"subject":"...","body":"..."},
 "call1":{"body":"..."},
 "call2":{"body":"..."},
 "pin":{"body":"..."}}
```

[VERIFIED: read `config/system_prompt.py` — OUTPUT block contains exactly these 8 keys]

Two named error classes are required:

```python
class MaxTokensError(Exception):
    """Raised when the model stops due to max_tokens limit."""

class OutputParseError(Exception):
    """Raised when the model response cannot be parsed as valid 8-key JSON."""


EMAIL_KEYS = ["e1", "e2", "e3", "e4", "e5"]
CALL_KEYS = ["call1", "call2", "pin"]
ALL_KEYS = EMAIL_KEYS + CALL_KEYS


def parse_output(response: anthropic.types.Message, contact_id: str) -> dict:
    if response.stop_reason == "max_tokens":
        raise MaxTokensError(
            f"Contact {contact_id}: model stopped at max_tokens (output truncated, response invalid)"
        )

    raw_text = response.content[0].text

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        # Save raw response before raising (GEN-06)
        raw_path = os.path.join(RUNNER_TEMP, f"raw_response_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(
            f"Contact {contact_id}: JSON parse failed — raw response saved to {raw_path}"
        ) from exc

    # Validate 8-key schema
    missing = [k for k in ALL_KEYS if k not in parsed]
    if missing:
        raw_path = os.path.join(RUNNER_TEMP, f"raw_response_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(
            f"Contact {contact_id}: missing keys {missing} — raw response saved to {raw_path}"
        )

    # Validate sub-keys
    for k in EMAIL_KEYS:
        if "subject" not in parsed[k] or "body" not in parsed[k]:
            raise OutputParseError(f"Contact {contact_id}: key {k} missing subject or body")
    for k in CALL_KEYS:
        if "body" not in parsed[k]:
            raise OutputParseError(f"Contact {contact_id}: key {k} missing body")

    return parsed
```

**Note on model output format:** The system prompt says "Return ONLY valid JSON, no preamble, no markdown fences". In practice, LLMs occasionally wrap in triple backticks. The linter (Phase 5) would catch this as a hard failure triggering regeneration, but for robustness the parser should strip a leading ` ```json ` / trailing ` ``` ` fence before calling `json.loads`. [ASSUMED — common defensive practice; not a spec requirement. Add as a one-liner strip before json.loads.]

---

### Pattern 4: Batch API mode (GEN-04)

[VERIFIED: Context7 /anthropics/anthropic-sdk-python — MessageBatch model fields, batches.create, batches.retrieve, batches.results, MessageBatchIndividualResponse fields]

The Batch API is a distinct code path behind `INPUT_USE_BATCH_API=true`. It does not use `@retry` on the submit call itself (the batch creation is synchronous and fast); retries are irrelevant for the poll loop.

**Batch processing_status values:** `"in_progress"`, `"canceling"`, `"ended"` — poll until `"ended"`.
**Batch expires_at:** Anthropic batches expire in 24 hours.
**Results:** `client.messages.batches.results(batch_id)` returns a `JSONLDecoder[MessageBatchIndividualResponse]`. Results are NOT guaranteed to be in submission order — match by `custom_id` (use `contact_id` as the `custom_id`).

```python
import time

def run_batch_mode(contact_ids: list, records: dict) -> None:
    requests = []
    for cid in contact_ids:
        brief = records[cid]
        requests.append({
            "custom_id": cid,
            "params": {
                "model": "claude-sonnet-5",
                "max_tokens": 3000,
                "system": SYSTEM_MESSAGE,
                "messages": [{"role": "user", "content": brief["brief_text"]}],
            },
        })

    batch = client.messages.batches.create(requests=requests)
    print(f"Batch submitted: {batch.id}  ({len(requests)} requests)")

    # Poll until ended
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        print(f"  Batch status: {batch.processing_status}  "
              f"(processing={batch.request_counts.processing}, "
              f"succeeded={batch.request_counts.succeeded}, "
              f"errored={batch.request_counts.errored})")
        time.sleep(60)

    print(f"Batch complete: succeeded={batch.request_counts.succeeded}, "
          f"errored={batch.request_counts.errored}")

    # Retrieve and dispatch results
    for item in client.messages.batches.results(batch.id):
        cid = item.custom_id
        if item.result.type == "succeeded":
            try:
                parsed = parse_output(item.result.message, cid)
                write_generated(cid, parsed, records[cid])
            except (MaxTokensError, OutputParseError) as exc:
                contact_email = records[cid].get("contact_email", "")
                write_dlq(cid, contact_email, "parse_output", str(exc), 0)
        else:
            contact_email = records[cid].get("contact_email", "") if cid in records else ""
            write_dlq(cid, contact_email, "batch_api_error",
                      f"result.type={item.result.type}", 0)
```

**Key detail on cache_control in batch requests:** The `system` field in each batch `params` dict is a list of `TextBlockParam` dicts with `cache_control`. Cache is shared across requests in the same batch that use identical system content, so the cache benefit applies in batch mode too. [ASSUMED — based on Anthropic documentation principle that prompt caching is content-based, not call-based. Risk if wrong: higher batch cost but no correctness impact.]

---

### Pattern 5: brief_{id}.json input schema (from Phase 3)

[VERIFIED: read `scripts/exclude_and_route.py` — `brief_payload` structure at line 388–396]

```python
# Schema of brief_{id}.json written by Phase 3
{
    "contact_id": str,
    "brief_text": str,       # full §3.7 plain-text brief — this is the user message
    "case_study": str,       # e.g. "Systemnet (Sydney MSP)"
    "email3_url": str,
    "email4_url": str,
    "close_option": int,
    "close_text": str,
}
```

The user message is `brief["brief_text"]` verbatim. No transformation needed.

---

### Pattern 6: Per-contact loop structure (GEN-01, GEN-07, ERR-02)

Consistent with `fetch_record.py` and `exclude_and_route.py`:

```python
def main():
    ids_path = os.path.join(RUNNER_TEMP, "contact_ids.json")
    with open(ids_path) as f:
        contact_ids = json.load(f)

    # DLQ sentinel at startup (ERR-02)
    write_dlq("batch", "", "startup", "sentinel", 0)

    use_batch = os.environ.get("INPUT_USE_BATCH_API", "").lower() == "true"

    if use_batch:
        briefs = {}
        for cid in contact_ids:
            brief_path = os.path.join(RUNNER_TEMP, f"brief_{cid}.json")
            if os.path.exists(brief_path):
                with open(brief_path) as f:
                    briefs[cid] = json.load(f)
        run_batch_mode(list(briefs.keys()), briefs)
    else:
        for cid in contact_ids:
            brief_path = os.path.join(RUNNER_TEMP, f"brief_{cid}.json")
            if not os.path.exists(brief_path):
                continue   # excluded by Phase 3 — no brief written
            with open(brief_path) as f:
                brief = json.load(f)
            contact_email = brief.get("contact_email", "")
            try:
                response = _call_realtime(brief["brief_text"])
                parsed = parse_output(response, cid)
                write_generated(cid, parsed, brief)
            except (MaxTokensError, OutputParseError) as exc:
                write_dlq(cid, contact_email, "parse_output", str(exc), 0)
                print(f"ERROR {cid}: {exc}", file=sys.stderr)
            except Exception as exc:
                write_dlq(cid, contact_email, "generate_campaign", str(exc), 0)
                print(f"ERROR {cid}: {exc}", file=sys.stderr)
```

---

### Pattern 7: generated_{id}.json output schema (GEN-07)

```python
def write_generated(contact_id: str, parsed: dict, brief: dict) -> None:
    payload = {
        "contact_id": contact_id,
        "e1": parsed["e1"],
        "e2": parsed["e2"],
        "e3": parsed["e3"],
        "e4": parsed["e4"],
        "e5": parsed["e5"],
        "call1": parsed["call1"],
        "call2": parsed["call2"],
        "pin": parsed["pin"],
        # Carry-through metadata for Phase 5 (lint) and Phase 6 (write-back)
        "case_study": brief.get("case_study", ""),
        "close_option": brief.get("close_option"),
        "close_text": brief.get("close_text", ""),
    }
    out_path = os.path.join(RUNNER_TEMP, f"generated_{contact_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
```

Carrying `case_study`, `close_option`, and `close_text` forward into `generated_{id}.json` is necessary so Phase 5 (lint) can: (a) check the close bank match (hard check 9), (b) check the `[Insert {case study name} case study link here]` placeholder in Phase 5 (ASSEM-01), and (c) check e2 for the correct case study name.

---

### Anti-Patterns to Avoid

- **Passing system prompt as plain string:** `system="..."` works but loses cache benefit. Always pass as `list[TextBlockParam]` with `cache_control`.
- **Parsing response.content directly as string:** `response.content` is a list of `ContentBlock` objects. The text is at `response.content[0].text`.
- **Assuming batch result order:** Batch results stream in arbitrary order. Match by `custom_id`, not by position.
- **Calling `json.loads` on raw text without stripping potential markdown fences:** The system prompt forbids fences but defensive stripping costs nothing.
- **Writing generated_{id}.json before parse succeeds:** Write only after `parse_output` returns cleanly (same atomic pattern used in fetch_record.py for contact_{id}.json).
- **Using `@retry` on the batch poll loop:** The poll loop calls `retrieve()` every 60 seconds; wrapping it in retry would interfere with the sleep-and-check pattern. Only the `create` call and the individual realtime call need retry.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Retry on 429/5xx | Custom retry loop | `@retry(**ANTHROPIC_RETRY_KWARGS)` from utils.py | Already handles Retry-After header, jitter, max attempts |
| DLQ tracking | Custom error log | `write_dlq()` from utils.py | Zero-divergence policy; consistent schema across all scripts |
| JSON decode of model response | Custom parser | `json.loads()` + named exceptions | The model is instructed to return pure JSON; standard library handles it |
| Batch polling | Custom async client | `client.messages.batches.retrieve()` + `time.sleep(60)` | SDK provides direct retrieval; polling is the correct pattern for this use case |

---

## Common Pitfalls

### Pitfall 1: max_tokens discrepancy between spec and requirements

**What goes wrong:** Spec §5.1 says `max_tokens=2000`. REQUIREMENTS.md GEN-03 says `max_tokens=3000`. Using 2000 may truncate the 8-key output (5 emails + 3 call notes, each up to 110 words = ~1700 tokens of output minimum).

**Why it happens:** v1.1 amendment added call1/call2/pin to the OUTPUT block, increasing expected output length. The spec §5.1 table was not updated.

**How to avoid:** Use `max_tokens=3000` per REQUIREMENTS.md GEN-03. REQUIREMENTS.md is the authoritative v1 spec.

**Warning signs:** `stop_reason == "max_tokens"` on early contacts during pilot.

---

### Pitfall 2: System prompt not reaching cache threshold

**What goes wrong:** Prompt caching silently does nothing if the cached content is under the minimum token threshold (~1024 tokens for claude-sonnet models). The system prompt is approximately 1500 tokens, which should exceed the minimum, but if the SDK version or model changes the threshold, there is no error — just higher cost.

**How to avoid:** Check `response.usage.cache_creation_input_tokens` on the first call. If it is 0 after first call and 0 on second call, caching is not activating. Log `response.usage` in debug output.

**Warning signs:** `cache_read_input_tokens` is consistently 0 on calls after the first.

---

### Pitfall 3: brief_{id}.json only exists for passing contacts

**What goes wrong:** `contact_ids.json` lists ALL contacts (including those excluded in Phase 3). `brief_{id}.json` only exists for contacts that passed all filters. If the loop iterates `contact_ids.json` and attempts to open every brief, it will `FileNotFoundError` on excluded contacts.

**How to avoid:** Guard with `if not os.path.exists(brief_path): continue` before opening. This is the correct pattern — excluded contacts have no brief and no generated output.

**Warning signs:** `FileNotFoundError: brief_12345.json` for contacts that appear in `exclusion_report.json`.

---

### Pitfall 4: Batch results not in submission order

**What goes wrong:** Code that writes `generated_{id}.json` by iterating batch results in order and assuming position = contact_id position will corrupt outputs.

**How to avoid:** Always use `item.custom_id` to identify which contact the result belongs to. Set `custom_id=contact_id` when building batch requests.

**Warning signs:** Generated output for wrong contact (subject mentions wrong name).

---

### Pitfall 5: Model wraps JSON in markdown fences

**What goes wrong:** Despite the system prompt instructing "no preamble, no markdown fences", the model occasionally outputs ` ```json\n{...}\n``` `. `json.loads` fails on this, triggering `OutputParseError` and saving an unnecessary DLQ entry.

**How to avoid:** One-liner pre-strip before json.loads:
```python
text = raw_text.strip()
if text.startswith("```"):
    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
```

**Warning signs:** `OutputParseError` where raw_response file starts with ` ``` `.

---

### Pitfall 6: brief_text contains contact_email only in contact_{id}.json, not brief_{id}.json

**What goes wrong:** `brief_{id}.json` does not carry `contact_email` (verified by reading `exclude_and_route.py` — the payload at line 388 only has: `contact_id`, `brief_text`, `case_study`, `email3_url`, `email4_url`, `close_option`, `close_text`). `write_dlq` requires `contact_email` for the Teams notification.

**How to avoid:** For the realtime loop, `contact_email` is not available from `brief_{id}.json` alone. Two options: (a) load the corresponding `contact_{id}.json` to get the email, or (b) pass `""` as the email to `write_dlq` (consistent with how Phase 3 handles routing errors at line 403). Option (b) is simpler and the pattern used in Phase 3 — use it.

---

## Code Examples

### Full realtime call with cache_control

```python
# Source: verified against anthropic SDK 0.122.0 + Context7 /anthropics/anthropic-sdk-python
import anthropic
import json
import os
import sys
from tenacity import retry
from config.system_prompt import get_system_prompt
from utils import ANTHROPIC_RETRY_KWARGS, write_dlq

RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_MESSAGE = [
    {
        "type": "text",
        "text": get_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]

@retry(**ANTHROPIC_RETRY_KWARGS)
def _call_realtime(brief_text: str) -> anthropic.types.Message:
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=3000,
        system=SYSTEM_MESSAGE,
        messages=[{"role": "user", "content": brief_text}],
    )
```

### Batch submit and poll

```python
# Source: verified against anthropic SDK 0.122.0 — MessageBatch.processing_status values
# and client.messages.batches.create / retrieve / results

import time

def submit_and_poll(requests: list) -> str:
    """Submit batch, poll until ended, return batch_id."""
    batch = client.messages.batches.create(requests=requests)
    print(f"Batch {batch.id} submitted ({len(requests)} requests)")
    while True:
        batch = client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        print(f"  {batch.processing_status}: {batch.request_counts.processing} processing, "
              f"{batch.request_counts.succeeded} done, {batch.request_counts.errored} errored")
        time.sleep(60)
    return batch.id
```

### Retrieve and dispatch batch results

```python
# Source: verified — MessageBatchIndividualResponse.result.type values:
# "succeeded" | "errored" | "canceled" | "expired"
# MessageBatchSucceededResult.message is a full anthropic.types.Message object

def dispatch_batch_results(batch_id: str, briefs: dict) -> None:
    for item in client.messages.batches.results(batch_id):
        cid = item.custom_id
        if item.result.type == "succeeded":
            try:
                parsed = parse_output(item.result.message, cid)
                write_generated(cid, parsed, briefs[cid])
            except (MaxTokensError, OutputParseError) as exc:
                write_dlq(cid, "", "parse_output", str(exc), 0)
        else:
            write_dlq(cid, "", "batch_api_error",
                      f"result.type={item.result.type}", 0)
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Batch API in beta namespace (`client.beta.messages.batches`) | GA namespace (`client.messages.batches`) | SDK ~0.30+ | Use `client.messages.batches`, not `client.beta.messages.batches` |
| `cache_control` as top-level `messages.create` param | `cache_control` on individual `TextBlockParam` content blocks | Current SDK | Must pass `system` as `list[TextBlockParam]`, not plain string |
| `cache_control: {"type": "ephemeral"}` with no TTL | TTL now optional (`"5m"` default or `"1h"`) | SDK 0.100+ | Omitting TTL defaults to 5 minutes — adequate for a single pipeline run |

**Deprecated/outdated:**
- `client.beta.messages.batches`: The beta namespace still works as a passthrough but is not needed. Use GA namespace.
- Passing `system` as a plain `str` when caching is desired: Works (no error) but caching silently does not activate.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Prompt caching minimum token threshold is ~1024 tokens for claude-sonnet models | Pitfall 2, Pattern 1 | Cache silently inactive — higher API cost only, no correctness impact |
| A2 | cache_control on system message applies to batch requests with identical system content | Pattern 4 | Higher batch cost only, no correctness impact |
| A3 | Stripping markdown fences before json.loads is defensive but not required by spec | Anti-Patterns / Pitfall 5 | OutputParseError on occasional model outputs; easy to add as one-liner |

---

## Open Questions (RESOLVED)

1. **contact_email availability in generate_campaign.py** — RESOLVED: Use `""` throughout, consistent with Phase 3's routing error pattern. `brief_{id}.json` carries no email field; loading `contact_{id}.json` as a side-effect is out of scope for v1.

2. **Poll interval for batch mode** — RESOLVED: `time.sleep(60)` per poll iteration. Encoded as a requirement in Plan 04-02 acceptance criteria.

3. **Whether to log cache hit/miss per call** — RESOLVED: Print `cache_read_input_tokens` and `cache_creation_input_tokens` on the first contact only (`print()` level, not stderr) to confirm caching is active, then suppress for subsequent contacts.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| anthropic Python SDK | GEN-01–07 | Yes | 0.122.0 | — |
| ANTHROPIC_API_KEY env var | All API calls | Set in GitHub Actions secrets | — | Pipeline fails fast if missing |
| RUNNER_TEMP env var | File I/O | Defaults to `.` if unset | — | Falls back to CWD |
| passing_ids.json | main() loop | Written by Phase 3 exclude_and_route.py (amended in Plan 04-01) | — | Pipeline fails fast if missing |
| brief_{id}.json | Per-contact | Written by Phase 3 for passing contacts only | — | `continue` if not found (excluded contact) |
| tenacity | Retry decorator | Yes (installed with requirements.txt) | >=9.0.0 | — |

**No missing dependencies with no fallback** (beyond ANTHROPIC_API_KEY which is a GitHub Actions secret).

---

## Validation Architecture

> `nyquist_validation: false` in `.planning/config.json` — this section is SKIPPED.

---

## Security Domain

This phase makes outbound API calls with `ANTHROPIC_API_KEY`. The key is stored as a GitHub Actions secret and read via `os.environ["ANTHROPIC_API_KEY"]` (fail-fast if missing). No user input reaches the API call — `brief_text` is assembled entirely from validated HubSpot data by Phase 3 deterministic code. No SQL, no shell injection surface. No new security controls are needed beyond what is already established in the pipeline.

---

## Sources

### Primary (HIGH confidence)
- `/anthropics/anthropic-sdk-python` (Context7) — TextBlockParam, CacheControlEphemeralParam, messages.create signature, MessageBatch model, batches.create/retrieve/results, MessageBatchIndividualResponse, MessageBatchSucceededResult/ErroredResult, stop_reason values
- `scripts/utils.py` (read directly) — ANTHROPIC_RETRY_KWARGS, write_dlq signature
- `config/system_prompt.py` (read directly) — 8-key OUTPUT schema confirmed
- `scripts/exclude_and_route.py` (read directly) — brief_{id}.json schema confirmed
- `anthropic==0.122.0` (installed, verified via python -c)

### Secondary (MEDIUM confidence)
- `SD_Reengagement_LaneA_Build_Spec.md` §5.1 — model config (cross-referenced against REQUIREMENTS.md for max_tokens discrepancy)
- `.planning/REQUIREMENTS.md` GEN-01–07 — authoritative v1 requirement set

### Tertiary (LOW confidence)
- None required; all material claims are HIGH-confidence verified.

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — installed SDK version confirmed; all SDK patterns verified via Context7 against live source
- Architecture: HIGH — all patterns cross-verified against existing scripts in the codebase
- Pitfalls: MEDIUM-HIGH — pitfalls 1/3/4/6 are verified against actual code/spec; pitfalls 2/5 are ASSUMED from common practice

**Research date:** 2026-08-27
**Valid until:** 2026-09-27 (stable SDK; 30 days)
