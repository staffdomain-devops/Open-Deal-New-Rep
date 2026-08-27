---
phase: 07-cicd
plan: 01
status: complete
completed_at: "2026-08-27"
files_created:
  - scripts/assemble_output.py
---

# 07-01 Summary — assemble_output.py

## What was built

`scripts/assemble_output.py` — terminal aggregator that reads `lint_passing_ids.json` and merges every `assembled_{cid}.json` into a single `campaign_output.json` array.

## Verification

- `ast.parse` syntax check: PASS
- Functional test (2 contacts, temp dir): exit 0, array length 2, PASS
- All required strings present: `write_dlq`, `lint_passing_ids.json`, `assembled_`, `campaign_output.json`, `startup`, `assemble_output`

## Pattern conformance

Mirrors `assemble_bodies.py` exactly:
- `RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")`
- DLQ sentinel before contact loop
- Per-contact try/except with `FileNotFoundError` → SKIP, other exceptions → `write_dlq` + continue
- `json.dump(..., default=str)`
- Summary print on completion
- `if __name__ == "__main__": main()` guard
