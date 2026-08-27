---
phase: 05-lint-assembly
plan: 01
status: complete
completed: "2026-08-27T00:00:00Z"
commit: 902ebfd
---

# Summary — 05-01: Lint Engine

## What was built

Created `scripts/lint.py` with the full lint engine for Phase 5.

## Files

| File | Action | Lines |
|------|--------|-------|
| scripts/lint.py | Created | 557 |

## Tasks completed

1. All 18 hard check functions (H01–H18) — 12 from spec v1.0 §7.1, 6 from v1.1 amendments
2. All 5 soft warning functions (W01–W05) from spec §7.2
3. `run_lint()` orchestrator — runs all checks, returns (hard_failures, soft_warnings)
4. `_write_to_review_sample()` — reads existing file, appends entry, writes back (never overwrites)
5. `_regenerate_contact()` — calls _call_realtime + parse_output + write_generated
6. `main()` — DLQ sentinel at startup, lint loop with one regen attempt, writes lint_passing_ids.json

## Key decisions

- WORD_COUNT_MIN=40, WORD_COUNT_MAX=120 per spec §7.1 check 8 (not system prompt's 45–110)
- H10 name invention check: mid-sentence title-case pattern `(?<=[a-z]\s)([A-Z][a-z]{1,})` + COMMON_CAPS frozenset to avoid sentence-start and structural-word false positives
- H12 INTERNAL leak: first 3 significant tokens (3+ letters, excluding stopwords) per sensitive item
- _write_to_review_sample reads existing file first (append semantics, not overwrite)
- Every 10th passing contact lands in review_sample.json with reason "Nth sample"
