# Phase 2: Record Assembly - Context

**Gathered:** 2026-08-21
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 2 delivers two scripts:

- `scripts/fetch_list.py` — accepts `INPUT_LIST_ID` env var, fetches all contact IDs from the HubSpot list via the Lists API, writes them to a RUNNER_TEMP hand-off file for the per-contact loop.
- `scripts/fetch_record.py` — for a single contact ID, fetches and assembles the full per-contact research package from HubSpot (contact props, company props, all company contacts, company deals, story notes, live hiring signals, handover resolution, geo resolution, departure check, only-contact flag, sensitive items). Writes `$RUNNER_TEMP/contact_{id}.json`.

Phase 2 does not apply exclusion filters (Phase 3), assemble the brief (Phase 3), call the model (Phase 4), or write back to HubSpot (Phase 6).

</domain>

<decisions>
## Implementation Decisions

### Script Structure
- **D-01:** Two separate scripts — `scripts/fetch_list.py` (FETCH-01) and `scripts/fetch_record.py` (FETCH-02–11). REQUIREMENTS.md names them separately; the CI workflow treats list-fetch as a discrete step before the per-contact loop.
- **D-02:** `fetch_list.py` writes contact IDs to `$RUNNER_TEMP/contact_ids.json` (list of strings). `fetch_record.py` is invoked once per contact ID and writes `$RUNNER_TEMP/contact_{id}.json`.

### HubSpot API Approach
- **D-03:** Use the **v1 legacy engagements endpoint** (`/engagements/v1/engagements/associated/CONTACT/{id}/paged`) via direct `requests` calls (with `REQ_RETRY_KWARGS` from `utils.py`) for fetching CALL and EMAIL engagement objects (FETCH-06 notes and FETCH-07 handover). Reason: simpler single-endpoint pagination that returns type + owner + timestamp + direction in one response; consistent with the Inbound pipeline pattern; avoids the multi-hop association lookups required by v3 CRM objects.
- **D-04:** All other HubSpot reads (contact, company, company contacts, company deals, owners) use the `hubspot-api-client` SDK (`hubspot.crm.contacts`, `hubspot.crm.companies`, `hubspot.crm.deals`, `hubspot.crm.owners`).
- **D-05:** For FETCH-04 (all company contacts): fetch via company associations API (`hubspot.crm.companies.associations_api.get_all`) then batch-read contact properties.

### Geo Resolution (FETCH-08)
- **D-06:** Implement a 3-step resolution ladder (approximating the Lane B v2.1 §3.6.1 pattern that v1.1 references):
  1. Check `company.country` property against known string values: "Australia" / "AU" → `AU`; "New Zealand" / "NZ" → `NZ`; "United States" / "US" / "USA" / "United States of America" → `US`; "United Kingdom" / "UK" / "GB" / "Great Britain" → `UK`.
  2. If country property is blank or unmatched: fall back to contact `phone` prefix — `+61` → `AU`; `+64` → `NZ`; `+1` → `US`; `+44` → `UK`.
  3. If neither resolves: write `geo: "UNRESOLVED"` and add contact to a data-error hold list; do not pass to generation.
- **D-07:** This ladder is a best-effort implementation pending JP providing Lane B v2.1 §3.6.1. If the real ladder differs materially, update `fetch_record.py` before full-list run. Flag this as a pilot validation item.

### Sensitive Items Detection (FETCH-06 / FETCH-11)
- **D-08:** Apply conservative keyword-pattern detection on all retained `story_notes` to populate `sensitive_items`. Flag a note excerpt (first 300 chars) if it matches any of:
  - Negative language about prospect's people or business alongside named individuals (patterns: "mad", "annoyed", "frustrated", "angry", "furious", "repeating", "same errors", "complained", "blames", "blamed" within 80 chars of a proper noun or "CEO", "MD", "CFO", "director", "manager")
  - Competitor provider mentions (any proper noun following "using", "switched to", "went with", "chose", "preferred", "contract with", "working with" when not referring to Staff Domain)
  - Explicit sensitivity markers already in the notes (phrases beginning with "INTERNAL" or "confidential")
- **D-09:** False positives (over-flagging) are preferred over misses. Phase 3 (ROUTE-03) will wrap flagged items in `INTERNAL - NEVER REFERENCE` in the brief. The `sensitive_items` list contains raw excerpts, not reformatted text.
- **D-10:** If zero items are flagged, `sensitive_items` is an empty list `[]` — never omit the field.

### Notes Fetch Scope (FETCH-06)
- **D-11:** Fetch notes for the contact themselves plus the top **2** most-contacted colleagues (by `num_contacted_notes` desc, min 1). If the contact is the only one with `num_contacted_notes >= 1`, fetch notes for that contact only.
- **D-12:** Bot-noise filter runs on every note body before any further processing. The five prefix patterns from spec §3.3 are checked against the first 80 chars. Filtered notes go to `story_notes`; job-ad alert notes go through secondary parsing to extract role + month for `live_hiring_signals`.

### Output Format (FETCH-11)
- **D-13:** `contact_{id}.json` schema exactly as ROADMAP.md success criterion 1 specifies:
  ```
  {
    "contact_props": {...},       // FETCH-02 properties
    "company_props": {...},       // FETCH-03 properties
    "all_company_contacts": [...], // FETCH-04: all contacts with contacted counts
    "deals": [...],              // FETCH-05: junk-filtered deals
    "story_notes": [...],        // FETCH-06: bot-noise-filtered notes
    "live_hiring_signals": [...], // FETCH-06: job-ad signals
    "handover": {                 // FETCH-07
      "first_name": "...",
      "last_contact_date": "...",
      "method": "call|email",
      "is_active": true|false
    },
    "geo": "AU|NZ|US|UK|UNRESOLVED", // FETCH-08
    "is_only_contact": true|false,   // FETCH-10
    "sensitive_items": [...],        // FETCH-06 + D-08
    "departure_flagged": false        // FETCH-09: true if notes suggest contact has left
  }
  ```
- **D-14:** `departure_flagged` is set `true` if any retained note body contains "has left", "no longer with", or "moved on from" within 30 chars of the contact's own first or last name. The contact is NOT excluded by `fetch_record.py` — exclusion is Phase 3's job — but the flag is available for the E5 edge-case check.

### Error Handling
- **D-15:** Both scripts use `utils.py` retry helpers unchanged. `fetch_record.py` calls `write_dlq()` on unrecoverable failure, passing the contact ID and failed step name. Partial assembly (e.g., company fetch fails) is a hard error — do not write a partial `contact_{id}.json`.

### Claude's Discretion
- Pagination strategy for `fetch_list.py` (cursor-based vs. offset) — use whatever the HubSpot Lists API v3 provides natively.
- Junk deal filter implementation details — substring match on dealname, case-insensitive, for the three patterns from spec §3.2.
- `handover` fallback when zero CALL and zero outbound EMAIL engagements exist — write `handover: null`; Phase 3 exclusion filter E5 handles the "no engagement history" case.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Primary Spec
- `SD_Reengagement_LaneA_Build_Spec.md` §3 — Stage 1 full spec: company-first approach (§3.1), deal history rules (§3.2), notes + bot-noise filter + sensitive content (§3.3), handover resolution method (§3.4), exclusion filter list reference (§3.5), geo check v1.0 (§3.6, superseded by v1.1)
- `SD_Reengagement_LaneA_Build_Spec.md` §3.7 — The assembled brief format (input contract); field order and exact label text matter for Phase 3

### v1.1 Amendment (takes precedence where it conflicts with v1.0)
- `New SDR - Deal old deal outreach.md` §Change log — lists all v1.0 → v1.1 changes
- `New SDR - Deal old deal outreach.md` REPLACES §3.6 — geo resolution: retire v1.0 phone-prefix paragraph, adopt Lane B v2.1 §3.6 ladder (AU/NZ/US/UK, unresolved → data-error hold). **Lane B v2.1 spec not in this repo — D-06 implements a best-effort ladder; verify with JP before full-list run.**
- `New SDR - Deal old deal outreach.md` REPLACES §2 — pipeline overview; max tokens raised to 3000; 8-key output schema

### Requirements & Traceability
- `.planning/REQUIREMENTS.md` FETCH-01–11 — canonical requirement IDs; success criteria 1–5 for Phase 2

### Shared Utilities
- `scripts/utils.py` — `write_dlq`, `HS_RETRY_KWARGS`, `REQ_RETRY_KWARGS`, `safe_truncate`; zero-divergence policy (do not modify)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `scripts/utils.py`: `write_dlq(contact_id, email, step, error, retry_count)` — call on unrecoverable fetch failure. `HS_RETRY_KWARGS` for hubspot-api-client SDK calls. `REQ_RETRY_KWARGS` for direct requests calls (v1 engagements endpoint). `safe_truncate(text, max_chars)` — use when storing note bodies to cap size.

### Established Patterns
- RUNNER_TEMP JSON hand-off: all inter-script data goes through `os.environ.get("RUNNER_TEMP", ".")` as the base path. `fetch_list.py` writes contact IDs there; `fetch_record.py` reads nothing from it (takes contact ID as arg) and writes `contact_{id}.json` there.
- Retry-on-429/5xx: wrap every HubSpot API call with `@retry(**HS_RETRY_KWARGS)` or `@retry(**REQ_RETRY_KWARGS)` from utils.py.

### Integration Points
- Phase 3 (`exclude_and_route.py`) reads `$RUNNER_TEMP/contact_{id}.json` — field names and types in D-13 are a contract between Phase 2 and Phase 3.
- Phase 7 (CI workflow, `campaign.yml`) invokes `fetch_list.py` first, then loops over contact IDs invoking `fetch_record.py` per contact.

</code_context>

<specifics>
## Specific Ideas

- User deferred all decisions to spec — no user-specific preferences beyond spec compliance.
- Geo resolution ladder (D-06/D-07) is a best-effort implementation. The real Lane B v2.1 §3.6.1 ladder should be validated with JP at pilot before full-list run. Pilot checklist item equivalent to launch checklist item 13.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 2-Record Assembly*
*Context gathered: 2026-08-21*
