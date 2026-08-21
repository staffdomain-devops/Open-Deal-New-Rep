---
phase: 01-scaffolding
plan: "03"
subsystem: config
tags: [system-prompt, claude-api, prompt-engineering, scaffolding]
dependency_graph:
  requires: []
  provides: [config/system_prompt.py]
  affects: [scripts/generate_campaign.py (Phase 4)]
tech_stack:
  added: []
  patterns: [module-level constant, single accessor function, triple-quoted string]
key_files:
  created: [config/system_prompt.py]
  modified: []
decisions:
  - "SYSTEM_PROMPT stored as module-level constant (not computed); get_system_prompt() is a trivial one-liner accessor — no logic, no imports, allows cache_control ephemeral pattern in Phase 4"
  - "v1.1 edits applied as surgical replacements, not re-typed in full, to minimise paraphrase risk"
metrics:
  duration: "8 minutes"
  completed: "2026-08-21"
---

# Phase 1 Plan 03: Encode System Prompt (v1.0 + v1.1) Summary

**One-liner:** Verbatim v1.0 system prompt + three v1.1 surgical edits (8-key OUTPUT schema with internal-notes paragraph, amended INTERNAL rule, COUNTRY geo block) encoded as `config/system_prompt.py` module constant.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Assemble and write config/system_prompt.py with v1.0 + v1.1 prompt (SCAF-05) | 310628c | config/system_prompt.py |

## What Was Built

`config/system_prompt.py` contains:

- `SYSTEM_PROMPT`: triple-quoted string constant holding the complete system prompt assembled from the v1.0 base (SD_Reengagement_LaneA_Build_Spec.md §5.2) with all three v1.1 surgical edits applied
- `get_system_prompt() -> str`: one-line accessor returning `SYSTEM_PROMPT`

No imports. No logic. No `__main__` block.

### v1.1 Edits Applied

**(a) OUTPUT block schema:** 5-key schema replaced with 8-key schema (`e1`–`e5` each with `subject`/`body`; `call1`, `call2`, `pin` each with `body`). Immediately after the schema, the internal-notes paragraph inserted verbatim from the amendment: "call1, call2 and pin are INTERNAL notes for the salesperson, never seen by the recipient..." (word-for-word).

**(b) INTERNAL rule amendment:** "Never quote or paraphrase anything the brief marks INTERNAL - NEVER REFERENCE." replaced with "Never quote or paraphrase anything the brief marks INTERNAL - NEVER REFERENCE in any email; in call1, call2 and pin it must appear, per the OUTPUT rules."

**(c) Geography block:** The two v1.0 geography sentences ("Australian English spelling. For Australian records...") replaced with the Lane B v2.1 COUNTRY resolution block verbatim: "The brief resolves COUNTRY to AU, NZ, US or UK. AU: Australian context is assumed, never stated. NZ, US, UK: nothing may assume Australia; market observations stay neutral. US records: avoid dialect-marked spellings entirely (write around them; say 'two weeks', never 'fortnight'). Australian English spelling everywhere else."

### Verification

All 10 automated assertions pass:

```
system_prompt OK
PASS
```

Key assertions verified:
- Opens with "You write re-engagement emails for Staff Domain"
- `THE CORE RULE: casualness comes from SENTENCE CONSTRUCTION` present
- `No em dashes anywhere` present
- `EMAIL 1, Day 1` and `EMAIL 5, Day 21` present
- `call1, call2 and pin are INTERNAL notes for the salesperson` present (edit a)
- `in any email` present in the INTERNAL rule (edit b)
- `The brief resolves COUNTRY to AU, NZ, US or UK` present (edit c)
- `get_system_prompt() == SYSTEM_PROMPT` (identity check passes)

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None. This module is a static constant, fully populated with the verbatim prompt text.

## Threat Surface Scan

No new network endpoints, auth paths, file access patterns, or schema changes. The module is a static Python constant — no threat surface introduced.

| Threat ID | Status |
|-----------|--------|
| T-03-01 (Tampering — SYSTEM_PROMPT content) | Mitigated: 10 assertions verify key phrases; any paraphrase breaks at least one |
| T-03-02 (Information Disclosure — INTERNAL tags in emails) | Mitigated: edit (b) asserted via `in any email` check |
| T-03-03 (Tampering — v1.1 edits omitted) | Mitigated: three dedicated assertions (edit a, b, c) all pass |

## Self-Check

- [x] `config/system_prompt.py` exists: confirmed
- [x] Commit 310628c exists: confirmed
- [x] All 10 automated assertions pass: confirmed
- [x] No imports in file: confirmed (AST check)
- [x] No `2000` token reference: confirmed

## Self-Check: PASSED
