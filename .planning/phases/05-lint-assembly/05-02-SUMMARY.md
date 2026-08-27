---
phase: 05-lint-assembly
plan: 02
status: complete
completed: "2026-08-27T00:00:00Z"
commit: 6a4ce84
---

# Summary — 05-02: Body Assembly

## What was built

Created `scripts/assemble_bodies.py`. lint.py already contained `_regenerate_contact` and `main()` from Plan 05-01 execution (both waves implemented in one pass).

## Files

| File | Action | Lines |
|------|--------|-------|
| scripts/assemble_bodies.py | Created | 68 |
| scripts/lint.py | No change needed — _regenerate_contact + main() already present | — |

## Tasks completed

1. `assemble_bodies.py` reads `lint_passing_ids.json` from RUNNER_TEMP
2. DLQ sentinel written at startup
3. E2 body appended with `\n[Insert {case_study} case study link here]`
4. E5 body appended with `\n[Insert rep booking link here]`
5. E1, E3, E4 bodies copied unchanged
6. `assembled_{cid}.json` written to RUNNER_TEMP per passing contact

## Key decisions

- `_append_placeholder(body, placeholder)` returns `body + "\n" + placeholder` — single new line per spec §6
- case_study interpolated from `generated.get("case_study", "case study")` — falls back gracefully if key absent
- Shallow copy of e2/e5 dicts before mutation; remaining keys (subject, etc.) preserved unchanged
