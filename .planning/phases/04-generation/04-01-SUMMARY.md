---
phase: 04-generation
plan: 01
subsystem: generation-realtime
tags: [python, anthropic, claude-sonnet-5, prompt-caching, json-schema, dlq, retry]

# Dependency graph
requires:
  - phase: 03-exclusion-and-routing/03-02
    provides: brief_{id}.json with contact_id, brief_text, case_study, close_option, close_text

provides:
  - scripts/exclude_and_route.py (amended: writes passing_ids.json after exclusion report)
  - scripts/generate_campaign.py (realtime API path: parse_output, write_generated, _call_realtime, main)
  - passing_ids.json (JSON list of contact IDs that cleared all filters)
  - generated_{id}.json (8-deliverable output per passing contact)

affects: [04-02-batch, 05-lint, 06-writeback]

# Tech tracking
tech-stack:
  added:
    - anthropic SDK (client.messages.create, realtime path)
  patterns:
    - "SYSTEM_MESSAGE built once at module level with cache_control={type:ephemeral} (amortised across all contacts)"
    - "Fence strip before json.loads: defensive, handles model that wraps JSON in ```json blocks"
    - "raw_response_{id}.txt saved to RUNNER_TEMP before raising OutputParseError (never uploaded)"
    - "DLQ sentinel written at startup (write_dlq('batch', '', 'startup', 'sentinel', 0))"
    - "cache usage printed on first contact only (cache_creation_input_tokens, cache_read_input_tokens)"

key-files:
  created:
    - scripts/generate_campaign.py
  modified:
    - scripts/exclude_and_route.py

key-decisions:
  - "passing_ids.json inserted after exclusion_report.json write, before routing loop — no logic disruption"
  - "contact_email='' in all write_dlq calls (brief_{id}.json carries no email field)"
  - "SYSTEM_MESSAGE is list[dict] (not list[TextBlockParam]) — avoids SDK type import at module level"
  - "MaxTokensError and OutputParseError defined as bare exception classes (no custom __init__)"

requirements-completed:
  - GEN-01
  - GEN-02
  - GEN-03
  - GEN-05
  - GEN-06
  - GEN-07

# Metrics
duration: <5min
completed: 2026-08-27
---

# Phase 4 Plan 01: Generation Realtime Path — Summary

**exclude_and_route.py amended (passing_ids.json write inserted); generate_campaign.py created with full realtime path: two exception classes, parse_output, write_generated, _call_realtime with retry, and main() with DLQ sentinel and per-contact error handling.**

## Performance

- **Duration:** ~5 min
- **Completed:** 2026-08-27
- **Tasks:** 2 of 2 complete
- **Files modified:** 1 (exclude_and_route.py); **Files created:** 1 (generate_campaign.py)
- **Commits:** `4867407`

## Accomplishments

**exclude_and_route.py amendment (Task 1):**
- Inserted passing_ids.json write block between exclusion_report.json write and routing loop
- Path: `os.path.join(RUNNER_TEMP, "passing_ids.json")`; writes JSON list of passing contact IDs
- Print confirms count and path on every run

**generate_campaign.py (Task 2):**
- Module constants: `ANTHROPIC_API_KEY` (fail-fast via `os.environ[]`), `RUNNER_TEMP`, `client`, `SYSTEM_MESSAGE` with `cache_control=ephemeral`, `EMAIL_KEYS`, `CALL_KEYS`, `ALL_KEYS`
- Two exception classes: `MaxTokensError`, `OutputParseError`
- `parse_output()`: stop_reason check → fence strip → json.loads → top-level 8-key check → sub-key validation for email (subject+body) and call (body) keys; saves raw response before raise on JSON/missing-key failures
- `write_generated()`: builds payload with contact_id, all 8 keys, and case_study/close_option/close_text carry-through; writes `generated_{contact_id}.json`
- `_call_realtime()`: `@retry(**ANTHROPIC_RETRY_KWARGS)`, `model="claude-sonnet-5"`, `max_tokens=3000`
- `main()`: reads passing_ids.json; DLQ sentinel at startup; batch stub (`INPUT_USE_BATCH_API`); realtime loop with per-contact try/except for MaxTokensError, OutputParseError, and bare Exception

## Threat Mitigations Applied

| Threat ID | Mitigation |
|-----------|------------|
| T-04-01 | ANTHROPIC_API_KEY read via os.environ[]; never logged or written to any output |
| T-04-03 | MaxTokensError raised before parse; contact goes to DLQ; run continues |
| T-04-04 | raw_response_{id}.txt written to RUNNER_TEMP only; never uploaded as artifact |
| T-04-05 | write_generated called only after parse_output returns cleanly (all 8-key + sub-key validation) |

## Deviations from Plan

None.

---
*Phase: 04-generation*
*Completed: 2026-08-27*
