# Domain Pitfalls — Staff Domain Re-Engagement Pipeline

**Domain:** Python pipeline — HubSpot API v3 + Anthropic Batch API + GitHub Actions
**Researched:** 2026-08-19
**Confidence:** HIGH (all critical claims verified against official documentation or official HubSpot community posts)

---

## Critical Pitfalls

Mistakes that cause silent data corruption, blocked runs, or a full re-write.

---

### Pitfall C1: HubSpot Rate Limit Arithmetic Underestimate (Stage 1 + Stage 7)

**What goes wrong:** Stage 1 makes 3–5 API calls per contact (company fetch, contacts-on-company fetch, deals fetch, notes fetch, engagements fetch). At 422 contacts that is ~1,700–2,100 requests before a single email is written. Stage 7 adds contact PATCH calls (one per batch of 100, so ~5 batches for 422 contacts) plus 3 engagement creates per contact (note + 2 tasks) = 1,266 additional calls. Total run: roughly 3,000–3,400 requests. At 100 requests/10 seconds burst (Free/Starter tier), the raw arithmetic fits — but that assumes perfectly even distribution with no retries.

**Why it happens:** The burst window is not a sustained throughput figure. Any brief spike (e.g., concurrent association lookups at Stage 1 start) empties the token bucket and triggers 429s. The pipeline has no built-in backoff.

**Consequences:** 429 errors mid-run. Without retry logic the pipeline fails silently or raises an unhandled exception. Contacts processed before the failure get emails written; contacts after do not. The HubSpot write-back is not idempotent for task engagements — re-running creates duplicate tasks.

**Prevention:**
- Implement exponential backoff with jitter on every HubSpot call. Honour the `Retry-After` header returned with 429s.
- Process contacts sequentially in Stage 1 (not in parallel threads) to avoid simultaneous bursts.
- If the account is on Free/Starter (100 req/10 sec, 250k/day), do a pre-run arithmetic check: 422 contacts × 5 calls = 2,110 Stage 1 requests. That is 1.4% of the daily budget — fine. But if a bug causes retries to spiral, it can consume the budget and block the portal for the rest of the day.
- Track the `X-HubSpot-RateLimit-Remaining` response header; log a warning when it drops below 20.

**Warning signs:** HTTP 429 in logs; `Retry-After` header appearing; run time growing non-linearly.

**Stage affected:** Stages 1 (assemble) and 7 (write-back).

---

### Pitfall C2: Search API Has a Separate, Stricter Rate Limit (Stage 1)

**What goes wrong:** If the pipeline uses `/crm/v3/objects/{type}/search` to fetch notes or engagements filtered by contact ID, it hits the CRM Search API rate limit: **4 requests per second, shared across all search endpoints**. This is independent of and more restrictive than the general 100/10s burst limit.

**Why it happens:** The general rate limit and the search rate limit are governed by separate buckets. Developers who test with small lists do not hit it; at 422 contacts with 1–2 search calls each, the search endpoint is called 422–844 times. At 4/second that is a minimum 105–211 seconds just in search calls — and any concurrency at all causes 429s.

**Consequences:** Prolonged run times or complete stage failure. Since search is typically used in Stage 1, the pipeline may stall before any generation occurs.

**Prevention:**
- Prefer the list-associations endpoint (`/crm/v3/objects/contacts/{id}/associations/{type}`) over search for fetching engagements and notes per contact. This does not use the search budget.
- If search must be used (e.g., filtering notes by content), rate-limit it explicitly to 3 requests/second with a `time.sleep(0.34)` between calls, or use a proper rate limiter class.
- Audit all `*/search` calls in the codebase before the first full run.

**Warning signs:** Intermittent 429 with `error_category: RATE_LIMITS` specifically mentioning search; run time proportional to contact count even at small scale.

**Stage affected:** Stage 1 (assemble — notes and engagements fetches).

---

### Pitfall C3: Anthropic Batch API Job Timeout — GitHub Actions Kills the Poller (Stage 4)

**What goes wrong:** The Anthropic Batch API SLA is "most batches finish within 1 hour" but the hard limit is 24 hours. If system load is high, a 500-contact batch could take 2–4 hours. The GitHub Actions job has a 6-hour hard ceiling. The pipeline's polling loop (`time.sleep(60); retrieve batch`) runs inside the same job. If the poller is still waiting when the 6-hour wall is reached, GitHub Actions kills the job with no cleanup. The batch continues processing on Anthropic's side — results are available for 29 days — but the pipeline has no record of the batch ID to retrieve those results later.

**Why it happens:** Batch results are only accessible via the batch ID. If the job is killed before the ID is persisted to a file, artifact, or environment variable, results are orphaned. The batch ID is not automatically emailed or notified anywhere.

**Consequences:** ~500 contacts have been billed (at 50% rate), results exist but are inaccessible, and the pipeline must be re-run in full — paying twice.

**Prevention:**
- Persist the batch ID immediately after `client.messages.batches.create()` — write it to a workflow artifact, a GitHub Actions output, or a local state file before entering the polling loop. This takes one line and costs nothing.
- Set an internal timeout inside the polling loop (e.g., `max_wait_seconds = 18000` = 5 hours) that breaks out gracefully, emits a Teams notification with the batch ID, and exits with a non-zero code. Humans can then retrieve results manually or trigger a recovery run.
- Use the `1h` TTL cache (`"cache_control": {"type": "ephemeral", "ttl": "1h"}`) for the system prompt in batch requests — not the default 5-minute TTL (see Pitfall C5).
- Consider splitting the 422-contact list into two batches of ~211 if run times are uncertain; smaller batches finish faster.

**Warning signs:** Batch status remains `in_progress` after 90 minutes; `expires_at` field on the batch object shows fewer than 3 hours remaining.

**Stage affected:** Stage 4 (generation — batch submission and polling).

---

### Pitfall C4: HubSpot Note Pinning Requires a Two-Step Write in the Correct Order (Stage 7)

**What goes wrong:** The v3 API does not have a dedicated "pin" endpoint. Pinning is accomplished by PATCHing the contact record with `{"properties": {"hs_pinned_engagement_id": "<note_id>"}}`. The note must already exist AND already be associated with the contact before this PATCH is sent. If either precondition is unmet, the PATCH silently succeeds (returns 200) but the pin does not take effect in the HubSpot UI.

**Why it happens:** The property sets to the ID value but HubSpot's backend validates the association on render, not on write. A write that references a non-associated engagement does not return an error — it just produces a broken state.

**Consequences:** The "pinned note" feature is silently broken. JP opens a contact record and the note is not pinned. The only way to detect this is manual UI spot-check. Writing the same contact PATCH again after verifying association fixes it, but the automation has no visibility into whether it worked.

**Prevention:**
- The write-back sequence for each contact in Stage 7 must be: (1) POST `/crm/v3/objects/notes` to create the note, (2) POST the note-to-contact association via `/crm/v4/associations/notes/contacts/batch/create` with association type ID 202, (3) PATCH `/crm/v3/objects/contacts/{id}` with `hs_pinned_engagement_id`. Never combine steps 1 and 3 in a single call without confirming step 2 completed.
- Add a post-write verification: after the PATCH, immediately GET the contact with `properties=hs_pinned_engagement_id` and assert the returned value matches the note ID. Log a warning if it does not.
- The spec notes "fallback is manual pin pass" — keep this as a documented fallback if the automation fails the verification check more than 5% of the time during pilot.

**Warning signs:** Notes created but not pinned in HubSpot UI; PATCH returning 200 but no visible pin; `hs_pinned_engagement_id` returning a different ID than expected on read-back.

**Stage affected:** Stage 7 (write-back — note creation and pinning).

---

### Pitfall C5: Prompt Cache TTL Default Changed to 5 Minutes — Destroys Batch Cost Savings (Stage 4)

**What goes wrong:** The spec relies on `cache_control: {type: "ephemeral"}` to cache the system prompt across all 422 batch requests, reducing cost by 90% on input tokens for the system prompt. As of March 6, 2026, the default ephemeral TTL dropped from 1 hour to **5 minutes**. In a 500-request batch processed concurrently, the cache entry written by the first few requests expires before the tail of the batch runs if processing is spread across more than 5 minutes.

**Why it happens:** Batch processing is asynchronous and concurrent. The Anthropic docs confirm cache hits in batches are "best-effort" — not guaranteed. With a 5-minute TTL and batch processing that may take 30–90 minutes, many requests will miss the cache and pay full input token price.

**Consequences:** The system prompt (~900 words, ~650 tokens) is billed at full price for most of the 500 requests instead of at cache-read price (0.1×). For Claude Sonnet 5 at $1/MTok input, 650 tokens × 500 contacts at full vs cache-read price is a cost difference of roughly $0.29 vs $0.03 — small in absolute terms but the principle matters. More significantly, the spec's cost estimate of "a few dollars" assumed high cache hit rates.

**Prevention:**
- Use the explicit 1-hour TTL: `"cache_control": {"type": "ephemeral", "ttl": "1h"}`. This costs 2× the base input price for the write (not the read), but the cache writes are few and the reads are many. The math still heavily favours caching.
- Verify cache hit rates on the first pilot run by checking `cache_read_input_tokens` vs `input_tokens` in the batch results. If the hit rate is below 60%, investigate.
- The batch docs confirm: "Users typically experience cache hit rates ranging from 30% to 98%." With a 1h TTL and a batch that finishes in under 1 hour, expect the upper end.

**Warning signs:** Batch response shows `cache_read_input_tokens` close to zero; cost per run significantly higher than projected.

**Stage affected:** Stage 4 (generation — batch submission).

---

### Pitfall C6: HubSpot Multi-Line Text vs Single-Line Text Property Type Mismatch Destroys Email Formatting (Stage 7)

**What goes wrong:** The spec requires `email_1_body` through `email_5_body` to be `fieldType: textarea` (`type: string`, `fieldType: textarea` in the Properties API). If these properties were created as `fieldType: text` (single-line), the API accepts the write — including newline characters — with no error. HubSpot strips the newlines at render time in the sequence editor. Every email arrives as a single undifferentiated block of text to the prospect.

**Why it happens:** Both fieldTypes are `type: string` in the API. Write calls do not validate fieldType. The data is stored, but the sequence editor's rendering engine treats `text` properties as single-line regardless of stored content.

**Consequences:** Emails are unreadable. This is not a Stage 7 failure — the write "succeeds". The failure surfaces when JP previews the sequence template or, worse, when the first email is sent to a prospect.

**Prevention:**
- Before the first write, call `GET /crm/v3/properties/contacts/email_1_body` (and each of the 10 properties) and assert `fieldType == "textarea"`. This is the "verify property type before write" check the spec mandates. Build this as a preflight guard that halts the pipeline with a clear error if any property has the wrong fieldType.
- If properties do not exist yet, create them explicitly via the Properties API with `type: string`, `fieldType: textarea`. Do not rely on auto-creation.
- The spec's launch checklist item "Body properties confirmed as multi-line text in HubSpot" is a manual pre-run step, but the pipeline should also enforce it programmatically.

**Warning signs:** Sequence step preview shows email as a single paragraph; `\n\n` characters visible in raw HubSpot UI property value but not rendered.

**Stage affected:** Stage 7 (write-back — contact property update) and sequence delivery.

---

## Moderate Pitfalls

### Pitfall M1: GitHub Actions workflow_dispatch Payload Size for 422 Contact IDs

**What goes wrong:** The workflow is triggered by HubSpot passing contact IDs as a JSON array in the `inputs` payload. A JSON array of 422 HubSpot contact IDs looks like `[12345678, 12345679, ...]`. HubSpot contact IDs are typically 8–11 digits. At 11 digits each plus delimiter characters, 422 IDs ≈ 422 × 13 chars = ~5,500 characters. The GitHub Actions total input payload limit is 65,535 characters. 422 contacts at current HubSpot ID lengths is well within this limit.

**Why it is still a risk:** The limit is not per-input but total across all inputs. If the triggering mechanism is modified later (e.g., adding metadata fields alongside the IDs), the total can creep up. Additionally, community reports confirm that individual string inputs are capped at **1,024 characters** in the UI trigger, though programmatic API triggers (which HubSpot workflows use) may not enforce this. A JSON array of 422 IDs at ~5,500 characters exceeds 1,024 and could silently truncate if the HubSpot workflow passes it as a single string input through the UI dispatch mechanism.

**Prevention:**
- When building the HubSpot workflow step that calls the GitHub API, use `repository_dispatch` rather than `workflow_dispatch` if the IDs need to be more than 1,024 characters per field. The `repository_dispatch` `client_payload` is a JSON body with no per-field character limit (governed only by the API body size, which is several MB).
- If staying with `workflow_dispatch`, verify empirically on the first pilot trigger that all 422 IDs are received intact in `${{ github.event.inputs.contact_ids }}` by logging `len(json.loads(contact_ids))` at the start of the run.
- Consider an alternative trigger pattern: the HubSpot workflow writes the contact IDs to a HubSpot custom object or external store (S3, GitHub issue), and the workflow reads from there rather than from the input payload.

**Warning signs:** Contact count in the pipeline log is less than the expected HubSpot list count; JSON parse error on the contact_ids input.

**Stage affected:** Trigger (workflow_dispatch payload) — pre-Stage 1.

---

### Pitfall M2: Task Engagement Owner Assignment Defaults to Wrong User (Stage 7)

**What goes wrong:** The spec requires call task engagements to be "assigned to contact owner." In the v3 Tasks API, `hubspot_owner_id` must be set explicitly. If the property is omitted, the task is created with no owner and appears in HubSpot's "unassigned tasks" view rather than the current rep's queue. The pipeline knows the contact owner ID (it was resolved in Stage 1 via the owners API) but only if that ID is threaded through to Stage 7.

**Why it happens:** The contact owner ID (`hubspot_owner_id` on the contact) is fetched in Stage 1 for the research brief. If Stage 7 constructs the task payload from the generation output alone (which does not contain owner metadata), the owner ID is missing.

**Consequences:** Tasks exist but are unassigned. No rep sees them in their task queue. The automation "worked" but the operational outcome is broken.

**Prevention:**
- The pipeline's internal data structure per contact must carry `contact_owner_id` from Stage 1 through to Stage 7. Verify this thread explicitly in code review.
- After task creation, GET the task and assert `hubspot_owner_id` is non-null.

**Warning signs:** Tasks appear in HubSpot under "No owner" filter; rep's task count does not increase after a run.

**Stage affected:** Stage 7 (write-back — task engagement creation).

---

### Pitfall M3: LLM Output Passes JSON.parse but Fails Business Rules Silently (Stage 5)

**What goes wrong:** The spec's 18 lint checks include several that are structurally valid JSON but semantically wrong — most dangerously, invented names (Lint check 10) and INTERNAL content leaking into copy (Lint check 12). A simple `json.loads()` passes, all keys are present, word counts are in range, but the output references a colleague named "Marcus" who does not appear in the brief, or mentions that "they've had issues with their previous provider" (paraphrasing a note tagged INTERNAL).

**Why it happens:** The model follows voice rules reliably under normal conditions, but on contacts with rich, ambiguous notes, it occasionally generalises. The token-overlap check for INTERNAL content (check 12) requires comparing output tokens against brief content — a non-trivial string matching problem. If the check is implemented as an exact substring match rather than a semantic or token-level check, it can miss paraphrases.

**Consequences:** A contact receives an email referencing a colleague who does not exist (provably embarrassing) or implying knowledge of confidential internal information (reputational risk). These failures do not cause system errors; they only surface on human review.

**Prevention:**
- Lint check 10 (invented name detector): extract all capitalised single-word tokens from the email output that look like given names (a simple heuristic: any title-case token 3–10 chars that is not in the subject line pattern) and assert each one appears in the brief's known-names list (contact firstname, colleague firstnames, previous rep firstname, "Staff Domain"). Flag, do not auto-pass.
- Lint check 12 (INTERNAL content leak): implement as a bag-of-distinctive-tokens check against each INTERNAL bullet, not just exact substring. Extract the 3 most distinctive nouns from each INTERNAL bullet and check for their presence in the output. A phrase like "changing leadership" is not in the note verbatim but shares tokens with "management was restructured" in the INTERNAL bullet.
- The spec's regenerate-once-then-flag is the right policy, but the regeneration prompt should explicitly state the failing lint check so the model has context.

**Warning signs:** Any lint check 10 or 12 failure on pilot run; human reviewer flags a name or fact not in the brief.

**Stage affected:** Stage 5 (lint) and Stage 4 (generation) on regeneration.

---

### Pitfall M4: Anthropic Batch Results Are Unordered and May Have Partial Failures (Stage 4/5)

**What goes wrong:** The batch results JSONL file is not returned in submission order. Each result carries a `custom_id` field for matching. If the pipeline assumes position-based ordering (e.g., iterating the results list and mapping to the input list by index), contacts get the wrong emails.

The second issue: a batch with 422 requests may have some `errored` or `expired` results. These are not billed, but the pipeline must handle the case where contact IDs 1–400 have `succeeded` results and IDs 401–422 have `expired` results (because they were submitted last and the 24-hour window closed). If the pipeline processes all succeeded contacts and writes to HubSpot before checking for gaps, the run completes without error but 22 contacts have no emails.

**Prevention:**
- Always match results by `custom_id`, never by index. Use a dict keyed on `custom_id` (which should be the HubSpot contact ID as a string).
- After retrieving batch results, assert `len(succeeded_results) + len(errored_results) + len(expired_results) == len(submitted_requests)`. If any results are `expired` or `errored`, emit them to the Teams notification and to the review file before proceeding with the succeeded set.
- For `errored` results where `error.type == "server_error"`, these can be retried via a real-time fallback call. For `invalid_request_error`, inspect and fix before re-submitting.

**Warning signs:** Contact count in the write-back step is less than the count submitted to the batch; `expired` count non-zero in batch `request_counts`.

**Stage affected:** Stages 4 (generation) and 5 (lint) — result retrieval and matching.

---

### Pitfall M5: Close Bank Wording Altered by Character-Level Lint Issues (Stage 5)

**What goes wrong:** Lint check 9 requires the assigned close to appear "verbatim, altered by no more than punctuation." The close bank contains phrases like "No doubt we'll speak properly down the track." If the model substitutes a comma for a full stop mid-close, or splits a sentence with an em dash (which is also a hard failure under check 2), the close fails check 9 AND check 2 simultaneously, triggering two regenerations on the same contact.

**Why it happens:** The system prompt instructs the model to use the close verbatim, but if a previous email in the same sequence has vocabulary overlap with the close (e.g., both mention "history"), the model adjusts the homework line and sometimes adjusts the close instead.

**Consequences:** Both regeneration attempts may fail the same checks if the root cause is the close bank wording conflicting with voice rules the model is also trying to satisfy. The contact ends up flagged after two failures with no usable output.

**Prevention:**
- Implement lint check 9 as: extract the assigned close string from the spec, strip punctuation from both it and the email 1 body, and check for substring match. This tolerates minor punctuation variation without failing.
- In the regeneration prompt, paste the exact close bank entry and say "USE THESE EXACT WORDS, change nothing."
- Pre-check the close bank itself for em dashes before the run — there are none in the current 5 closes, but verify after any spec update.

**Warning signs:** Same contact failing lint check 2 and 9 on both the original and regenerated output.

**Stage affected:** Stage 5 (lint) → Stage 4 (regeneration).

---

## Minor Pitfalls

### Pitfall m1: HubSpot Owners API Returns Inactive Owners Without Error

**What goes wrong:** Stage 1 uses the owners API to resolve the previous rep's name and the current rep's name. The endpoint returns inactive owners (those who have left the business) with `archived: true` but does not error. If the pipeline does not check `archived`, it may use the current owner's name but that owner is actually inactive — the "new rep" email would be signed by someone who has also left.

**Prevention:** After resolving `hubspot_owner_id`, assert `isActive == true` (or `archived == false`) for the current contact owner. If the current owner is archived, flag the contact for JP review rather than generating.

**Stage affected:** Stage 1 (assemble — owner resolution) and Exclusion filter E2.

---

### Pitfall m2: HubSpot Note Body Newline Handling in Sequence Tokens

**What goes wrong:** The spec uses `\n\n` between paragraphs in email body properties. The HubSpot knowledge base confirms that "line breaks included in multi-line text property values are not supported in certain tools, such as marketing emails, quotes, and sequences." This ambiguity needs to be tested on the pilot before full run.

**Prevention:** The spec's launch checklist item "paragraph breaks verified rendering in the sequence editor on 3 live records" directly addresses this. Do not skip it. If breaks do not render, investigate Rich Text (`fieldType: richtext`) as an alternative — Rich Text stores HTML and preserves formatting in sequences — but Rich Text has its own complexity (HTML generation, character encoding).

**Stage affected:** Stage 7 (write-back) and sequence rendering.

---

### Pitfall m3: Duplicate Task Creation on Pipeline Re-run

**What goes wrong:** The pipeline has no idempotency mechanism for task engagements. If a run partially fails and is re-triggered for the same contact IDs, two CALL tasks are created per contact (one from each run). HubSpot does not deduplicate tasks.

**Prevention:** Before creating tasks in Stage 7, query existing tasks associated with the contact and check for tasks with matching `hs_task_subject` and `hs_timestamp` within the same day. If found, skip creation. Alternatively, include a unique external reference in `hs_task_body` (e.g., pipeline run ID + contact ID) to allow detection of duplicates.

**Stage affected:** Stage 7 (write-back — task engagement creation).

---

### Pitfall m4: Pre-send Bracket Check Must Cover All Properties, Not Just Generated Ones

**What goes wrong:** The spec appends `[Insert {case study name} case study link here]` and `[Insert rep booking link here]` to email 2 and 5 bodies respectively. The pre-send guard checks for `[` in email properties. If the guard only runs on the 10 email properties and a future pipeline version adds a custom property that also carries a placeholder, the gap opens up.

**Prevention:** The bracket check in Stage 7 should iterate all properties being written in the current run, not a hardcoded list. One loop, all properties, any `[` character → halt that contact's write.

**Stage affected:** Stage 7 (pre-send guard).

---

## Phase-Specific Warnings

| Phase / Stage | Likely Pitfall | Mitigation |
|---|---|---|
| Stage 1 — HubSpot reads | Rate limit burst on concurrent requests (C1), search API separate limit (C2) | Sequential processing, explicit rate limiter |
| Stage 1 — Owner resolution | Inactive owner not flagged (m1) | Assert `isActive` after owner lookup |
| Stage 4 — Batch submission | Batch ID lost if job times out (C3), cache TTL 5-min default (C5) | Persist batch ID before polling loop; use `"ttl": "1h"` |
| Stage 4 — Result retrieval | Unordered results, partial failures undetected (M4) | Match by `custom_id`; assert result count |
| Stage 5 — Lint | INTERNAL content paraphrase leaks (M3), close bank regeneration loops (M5) | Token-overlap check; verbatim close in regeneration prompt |
| Stage 7 — Property write | Wrong fieldType silently destroys formatting (C6) | Preflight fieldType assertion on all 10 email properties |
| Stage 7 — Note pin | Two-step association requirement invisible from API response (C4) | Sequence: create → associate → PATCH; read-back verify |
| Stage 7 — Task creation | Unassigned tasks (M2), duplicates on re-run (m3) | Thread `contact_owner_id` through; idempotency check |
| Trigger — workflow_dispatch | Large payload with 422 IDs (M1) | Verify full ID count on first pilot trigger |

---

## Sources

- HubSpot API rate limits (official): https://developers.hubspot.com/docs/developer-tooling/platform/usage-guidelines
- HubSpot CRM Search API rate limit increase announcement: https://developers.hubspot.com/changelog/crm-search-api-rate-limit-increase
- HubSpot note pinning (community, confirmed pattern): https://community.hubspot.com/t5/APIs-Integrations/Change-Note-state-to-pinned-via-API/td-p/871232
- HubSpot property fieldType values (legacy properties guide): https://developers.hubspot.com/docs/api-reference/legacy/crm/properties/guide
- HubSpot multi-line text in sequences (community): https://community.hubspot.com/t5/Account-Settings/Spacing-for-multi-line-text-properties-used-as-personalized/m-p/399987
- Anthropic Batch API official docs: https://platform.claude.com/docs/en/build-with-claude/batch-processing
- Anthropic prompt caching official docs: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic prompt cache TTL change (March 2026): https://dev.to/whoffagents/anthropic-silently-dropped-prompt-cache-ttl-from-1-hour-to-5-minutes-16ao
- GitHub Actions workflow_dispatch input limits (community): https://github.com/orgs/community/discussions/120093
- GitHub Actions job timeout limit: https://docs.github.com/en/actions/reference/actions-limits
- LLM JSON production failure rates: https://www.digitalapplied.com/blog/llm-structured-output-json-reliability-production
