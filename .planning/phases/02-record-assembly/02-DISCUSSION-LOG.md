# Phase 2: Record Assembly - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-08-21
**Phase:** 2-Record Assembly
**Areas discussed:** Script structure, Geo resolution, Sensitive items detection, HubSpot engagements API

---

## Gray Areas Presented

| Area | Options Presented | Disposition |
|------|------------------|-------------|
| Script structure | Two scripts (fetch_list.py + fetch_record.py) vs. one combined | Claude decided per spec |
| Geo resolution | Lane B v2.1 ladder vs. v1.0 phone-prefix vs. best-effort ladder | Claude decided per spec |
| Sensitive items detection | Keyword regex / empty list + model / human pre-tag | Claude decided per spec |
| HubSpot engagements API | v1 legacy endpoint vs. v3 CRM objects | Claude decided per spec |

**User's response:** "do whatever you think is according to spec" — full discretion delegated to Claude for all areas.

---

## Script Structure

**Selected:** Two scripts — `fetch_list.py` (FETCH-01) and `fetch_record.py` (FETCH-02–11).

**Rationale:** REQUIREMENTS.md explicitly names `fetch_list.py` as a discrete requirement (FETCH-01). The CI workflow's pipeline step model (Phase 7) treats list-fetch as a separate step before the per-contact loop.

---

## Geo Resolution

**Selected:** 3-step best-effort ladder: company.country property → phone prefix → UNRESOLVED (data-error hold).

**Rationale:** v1.1 amendment retires v1.0's phone-prefix approach and references Lane B v2.1 §3.6.1, which is not in this repo. The ladder approximates the expected AU/NZ/US/UK resolution behavior. Flagged for JP validation at pilot before full-list run.

---

## Sensitive Items Detection

**Selected:** Conservative keyword-pattern scan on story_notes; false positives preferred over misses; Phase 3 wraps flagged items with INTERNAL - NEVER REFERENCE.

**Rationale:** Spec §3.3 requires data-layer tagging but defines no heuristic. An automated keyword-based approach (negative language patterns, competitor-indicator phrases) is the minimal viable implementation.

---

## HubSpot Engagements API

**Selected:** v1 legacy engagements endpoint (`/engagements/v1/engagements/associated/CONTACT/{id}/paged`) via direct requests for CALL and EMAIL engagements (FETCH-06, FETCH-07). SDK for all other reads.

**Rationale:** Single-endpoint pagination returns type + owner + timestamp + direction in one response. Avoids multi-hop v3 association lookups. Consistent with Inbound pipeline pattern.

---

## Claude's Discretion

All four gray areas were decided by Claude per spec. Key discretion calls:
- Geo ladder string values (country property normalisation)
- Keyword patterns for sensitive item detection
- `handover: null` when no engagements exist (E5 in Phase 3 handles exclusion)
- Pagination approach for fetch_list.py (whatever Lists API v3 provides natively)

## Deferred Ideas

None.
