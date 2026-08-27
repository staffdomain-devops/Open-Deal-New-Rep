---
phase: 07-cicd
plan: 02
status: complete
completed_at: "2026-08-27"
files_created:
  - .github/workflows/campaign.yml
human_checkpoint:
  task: Task 2 — Verify workflow triggers and artifact behavior
  status: pending
  resume_signal: "Type 'verified' when the workflow inputs and artifact behavior are confirmed"
---

# 07-02 Summary — campaign.yml

## What was built

`.github/workflows/campaign.yml` — GitHub Actions workflow that orchestrates the full Lane A pipeline triggered via `workflow_dispatch`.

## Structure

| Layer | Detail |
|-------|--------|
| Trigger | `workflow_dispatch` with `list_id` (required string) and `pilot_mode` (boolean, default true) |
| Pipeline | fetch-list → pilot-mode-cap → fetch-records → exclude-and-route → generate-campaign → lint → assemble-bodies → assemble-output → write-hubspot |
| Pilot mode | Inline Python: if `pilot_mode=true` and contacts > 20, slices to first 20 and prints warning |
| Artifacts (always) | `exclusion-report`, `review-sample` |
| Artifacts (success) | `campaign-output` with `retention-days: 7` |
| Artifacts (failure) | `failed-contacts` |
| Failure notification | `notify-teams` step: curl POST to `$TEAMS_WEBHOOK_URL` with `list_id`, `run_url`, `failed_step` |
| Secrets | `HUBSPOT_API_KEY`, `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL` from `${{ secrets.* }}` |

## Verification

- `yaml.safe_load` syntax check: PASS
- All 23 grep assertions: ALL PASS

## Human checkpoint required

Task 2 is a blocking human-verify gate. JP must:
1. Go to Actions → Campaign Pipeline → Run workflow
2. Confirm `list_id` (required text) and `pilot_mode` (boolean, default true) inputs appear
3. Trigger a pilot run and confirm the pilot-mode-cap step behaves correctly
4. Verify artifacts appear with the correct conditions on success/failure
5. Verify Teams notification on failure

Resume signal: type `verified` when confirmed, or describe any discrepancies.
