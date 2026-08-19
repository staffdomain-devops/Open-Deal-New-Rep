# Phase 1: Data Foundation - Context

**Gathered:** 2026-08-19
**Status:** Ready for planning

<domain>
## Phase Boundary

Bootstrap the Python package: `ContactBrief` dataclass stub, `HubSpotClient` class with encapsulated rate limiter, `RateLimiter` class, and all 7 stage module stubs importable from `run.py`. Smoke-tested against the live HubSpot portal to confirm authentication, a single contact fetch, and rate limiter burst behavior.

</domain>

<decisions>
## Implementation Decisions

### Rate Limiter Wiring
- **D-01:** Rate limiter is **encapsulated inside `HubSpotClient`** — callers (stage modules) call client methods directly and never interact with the rate limiter. `RateLimiter` is instantiated in `HubSpotClient.__init__` and called internally before every API request. Stage modules have no rate-limiter dependency.
- **D-02:** Two rate limiters needed — one for general API (100 req/10s) and one for CRM Search (4 req/s). Both live inside the client. `rate_limiter.py` may implement a single `RateLimiter` class that is instantiated twice with different params.

### ContactBrief Scope in Phase 1
- **D-03:** Phase 1 ships a **minimal stub dataclass** — enough fields for the package to import and type-check, but field completeness is Phase 2's job. At minimum: `contact_id: str` and a docstring referencing spec §3.7. Phase 2 expands all fields.
- **D-04:** `GeneratedOutput` Pydantic model also lives in `models.py` as a stub (correct class definition, no fields yet — Phase 4 populates it).

### Smoke Test Form
- **D-05:** Smoke tests live in `tests/smoke_test.py` — a standalone Python script (not a pytest suite). Run it directly: `python tests/smoke_test.py`. It must: (a) initialize `HubSpotClient` from env, (b) fetch one real contact, (c) run a 20-request burst against the rate limiter and confirm no 429. Not wired into CI yet (Phase 7 does that).

### Local Dev Secrets
- **D-06:** Use `python-dotenv` to load env vars from a `.env` file locally. Add `python-dotenv` to requirements (dev dep acceptable). Commit a `.env.example` with placeholder values for all three secrets (`HUBSPOT_TOKEN`, `ANTHROPIC_API_KEY`, `TEAMS_WEBHOOK_URL`). `.env` itself goes in `.gitignore`. GitHub Actions reads secrets as native env vars — no dotenv needed in CI.

### Claude's Discretion
- Rate limiter algorithm (token-bucket vs sliding-window): Claude decides based on implementation simplicity and correctness under burst load. Token-bucket is the natural fit for HubSpot's "N requests per T seconds" framing.
- Exact `run.py` stub structure: Claude decides how stage stubs are called (e.g., `stage1.assemble(contact_ids)` shape), consistent with the package structure in CLAUDE.md.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Pipeline Specification
- `SD_Reengagement_LaneA_Build_Spec.md` — v1.0 spec: system prompt (§5.2), close bank (§5.3), routing table (§4), lint checks (§7), brief field order (§3.7). Single source of truth for all pipeline logic.
- `New SDR - Deal old deal outreach.md` — v1.1 amendment: adds call task notes, pin note, 7-touch cadence, lint checks 13–18, max tokens 3000. Extends v1.0 where noted.

### Project Planning
- `.planning/REQUIREMENTS.md` — 44 v1 requirements with REQ-IDs; Phase 1 requirements are ASSM-09, INFRA-04, INFRA-05
- `.planning/ROADMAP.md` — Phase 1 success criteria (what must be TRUE before Phase 2 begins)
- `CLAUDE.md` — locked tech stack, package structure, package versions, critical constraints (cache TTL bug, HubSpot property type, Teams webhook format)

### Critical Bugs / Constraints (from CLAUDE.md)
- Cache TTL: must use `{"type": "ephemeral", "ttl": "1h"}` not bare `{"type": "ephemeral"}` — Phase 4 concern, note now
- HubSpot body properties must be `fieldType: textarea` — Phase 6 concern, preflight check required
- Teams webhook must be Power Automate URL — legacy `webhook.office.com` retired 2026-03-31

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield. No existing Python code in the repo.

### Established Patterns
- Package versions are pinned in CLAUDE.md: `anthropic==0.122.0`, `hubspot-api-client==12.0.0`, `pydantic==2.13.4`, `requests==2.32.3`. Use `requirements.txt` with exact pins (`==`).
- `hubspot-api-client` (not `hubspot-sdk`) — the distinction matters; wrong package is an unstable alpha.

### Integration Points
- `HubSpotClient` is the sole interface between all stage modules and HubSpot. Every subsequent phase (2–6) imports and uses it.
- `run.py` is the sole orchestrator entry point — never import stage modules from outside `run.py`.

</code_context>

<specifics>
## Specific Ideas

- User confirmed: proceed directly to planning without gray area discussion. All decisions above are Claude's defaults with full build authority.
- Sample run file exists at `Sample run.txt` — 5-contact pilot output. Useful reference for Phase 4+ but not needed in Phase 1.

</specifics>

<deferred>
## Deferred Ideas

- None — discussion stayed within phase scope.

</deferred>

---

*Phase: 1-Data Foundation*
*Context gathered: 2026-08-19*
