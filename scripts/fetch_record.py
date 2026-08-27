"""fetch_record.py — Per-contact record assembly for the Lane A re-engagement pipeline.

Usage:
    python scripts/fetch_record.py <contact_id>

Writes $RUNNER_TEMP/contact_{id}.json with all 11 D-13 fields assembled from HubSpot.
"""

import json
import os
import re
import sys
from datetime import datetime

import hubspot
import requests
from hubspot.crm.contacts import BatchReadInputSimplePublicObjectId
from hubspot.crm.deals import BatchReadInputSimplePublicObjectId as DealBatchReadInput
from tenacity import retry

from utils import write_dlq, HS_RETRY_KWARGS, REQ_RETRY_KWARGS, safe_truncate

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

CONTACT_PROPS = [
    "firstname",
    "lastname",
    "jobtitle",
    "company",
    "industry",
    "hubspot_owner_id",
    "email",
    "hs_email_optout",
    "num_contacted_notes",
    "notes_last_contacted",
    "country",
    "phone",
    "hs_email_bounce",
]

COMPANY_PROPS = ["name", "industry", "country"]

COLLEAGUE_PROPS = [
    "firstname",
    "lastname",
    "jobtitle",
    "num_contacted_notes",
    "notes_last_contacted",
]

DEAL_PROPS = ["dealname", "dealstage", "createdate", "closedate"]

BOT_NOISE_PREFIXES = [
    "A new opportunity is available for",
    "Prospect Smarter",
    "Enrichment Successful",
    "Link to Job Post",
    "Job url:",
    "job url:",
    "Job URL:",
    "Sent pattern interrupt email",
]

JOB_AD_PREFIXES = [
    "A new opportunity is available for",
    "Link to Job Post",
    "Job url:",
    "job url:",
    "Job URL:",
]

# ---------------------------------------------------------------------------
# Environment and SDK client (fail fast if key is missing)
# ---------------------------------------------------------------------------

HUBSPOT_API_KEY = os.environ["HUBSPOT_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")

client = hubspot.Client.create(access_token=HUBSPOT_API_KEY)

# ---------------------------------------------------------------------------
# Fetch functions (added in Task 2)
# ---------------------------------------------------------------------------


@retry(**HS_RETRY_KWARGS)
def fetch_contact(contact_id: str) -> dict:
    """Fetch contact properties for the given contact ID.

    Returns a dict of {prop_name: value} for each name in CONTACT_PROPS.
    Missing properties default to None.
    """
    result = client.crm.contacts.basic_api.get_by_id(
        contact_id, properties=CONTACT_PROPS
    )
    props = result.properties or {}
    return {p: props.get(p) for p in CONTACT_PROPS}


@retry(**HS_RETRY_KWARGS)
def fetch_company(contact_id: str) -> tuple:
    """Fetch the company associated with a contact and its properties.

    Returns (company_id: str, company_props: dict).
    Returns ("", {}) if no company association exists.
    """
    assoc = client.crm.contacts.associations_api.get_all(contact_id, "companies")
    results = assoc.results or []
    if not results:
        return ("", {})
    company_id = str(results[0].id)
    company = client.crm.companies.basic_api.get_by_id(
        company_id, properties=COMPANY_PROPS
    )
    props = company.properties or {}
    return (company_id, {p: props.get(p) for p in COMPANY_PROPS})


@retry(**HS_RETRY_KWARGS)
def fetch_all_company_contacts(company_id: str) -> list:
    """Fetch all contacts associated with the given company.

    Returns a list of dicts with keys: firstname, lastname, jobtitle,
    num_contacted_notes (int, default 0), notes_last_contacted.
    Returns [] if company_id is empty.
    """
    if not company_id:
        return []

    assoc = client.crm.companies.associations_api.get_all(company_id, "contacts")
    contact_ids = [r.id for r in (assoc.results or [])]
    if not contact_ids:
        return []

    batch_input = BatchReadInputSimplePublicObjectId(
        inputs=[{"id": str(cid)} for cid in contact_ids],
        properties=COLLEAGUE_PROPS,
    )
    batch_result = client.crm.contacts.batch_api.read(batch_input)

    contacts = []
    for item in batch_result.results or []:
        props = item.properties or {}
        try:
            n_contacted = int(props.get("num_contacted_notes") or 0)
        except (TypeError, ValueError):
            n_contacted = 0
        contacts.append(
            {
                "firstname": props.get("firstname"),
                "lastname": props.get("lastname"),
                "jobtitle": props.get("jobtitle"),
                "num_contacted_notes": n_contacted,
                "notes_last_contacted": props.get("notes_last_contacted"),
                "_id": str(item.id),
            }
        )
    return contacts


@retry(**HS_RETRY_KWARGS)
def fetch_deals(company_id: str) -> list:
    """Fetch all junk-filtered deals associated with the given company.

    Returns a list of dicts with keys: dealname, dealstage, createdate, closedate.
    Returns [] if company_id is empty.
    """
    if not company_id:
        return []

    assoc = client.crm.companies.associations_api.get_all(company_id, "deals")
    deal_ids = [r.id for r in (assoc.results or [])]
    if not deal_ids:
        return []

    batch_input = DealBatchReadInput(
        inputs=[{"id": str(did)} for did in deal_ids],
        properties=DEAL_PROPS,
    )
    batch_result = client.crm.deals.batch_api.read(batch_input)

    deals = []
    for item in batch_result.results or []:
        props = item.properties or {}
        dealname = props.get("dealname") or ""
        name_lower = dealname.lower()
        # Junk filter: (Test), (delete), standalone 'test' token
        if name_lower.startswith("(test)") or name_lower.startswith("(delete)"):
            continue
        if re.search(r"\btest\b", dealname, re.IGNORECASE):
            continue
        deals.append(
            {
                "dealname": dealname,
                "dealstage": props.get("dealstage"),
                "createdate": props.get("createdate"),
                "closedate": props.get("closedate"),
            }
        )
    return deals


def _fetch_engagements_paged(person_id: str) -> list:
    """Fetch all engagements for a contact via the v1 legacy endpoint.

    Uses REQ_RETRY_KWARGS-decorated inner function for retry logic.
    Returns a flat list of raw engagement dicts.
    """

    @retry(**REQ_RETRY_KWARGS)
    def _get_page(offset: int) -> dict:
        resp = requests.get(
            f"https://api.hubapi.com/engagements/v1/engagements/associated/CONTACT/{person_id}/paged",
            params={"count": 100, "offset": offset},
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
        )
        resp.raise_for_status()
        return resp.json()

    all_results = []
    offset = 0
    while True:
        data = _get_page(offset)
        all_results.extend(data.get("results", []))
        if not data.get("hasMore", False):
            break
        offset = data["offset"]
    return all_results


def _parse_hiring_signal(body: str) -> dict:
    """Best-effort extraction of role + month from a job-ad note body.

    Returns {"role": str, "month": str} — falls back to the raw first line
    if specific patterns are not found.
    """
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
        "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

    # Try to extract role after "for" or "opportunity:"
    role = None
    role_match = re.search(
        r"(?:for|opportunity:)\s+([A-Z][^(\n]{2,60}?)(?:\s*[\(\n]|$)", body
    )
    if role_match:
        role = role_match.group(1).strip().rstrip(".,")

    # Try to find nearest month name
    month = None
    for m in month_names:
        if m in body:
            month = m
            break

    if not role:
        # Fall back to first non-empty line
        for line in body.splitlines():
            line = line.strip()
            if line:
                role = line
                break

    return {"role": role or "", "month": month or ""}


def _is_sensitive(body: str) -> bool:
    """Return True if any of the three sensitive-content patterns match the note body."""
    # a. Negative language near title/name
    if re.search(
        r"(?:mad|annoyed|frustrated|angry|furious|repeating|same errors|"
        r"complained|blames|blamed).{0,80}"
        r"(?:CEO|MD|CFO|director|manager|[A-Z][a-z]+)",
        body,
        re.IGNORECASE,
    ):
        return True

    # b. Competitor mention (not preceded by "Staff Domain" / "StaffDomain" within 60 chars)
    comp_match = re.search(
        r"(?:using|switched to|went with|chose|preferred|contract with|working with)"
        r"\s+([A-Z][A-Za-z\s]{2,30})",
        body,
        re.IGNORECASE,
    )
    if comp_match:
        start = comp_match.start()
        preceding = body[max(0, start - 60):start]
        if "Staff Domain" not in preceding and "StaffDomain" not in preceding:
            return True

    # c. Explicit sensitivity marker
    if re.search(r"\b(?:INTERNAL|confidential)\b", body, re.IGNORECASE):
        return True

    return False


def fetch_notes(
    contact_id: str, contact_props: dict, all_company_contacts: list
) -> tuple:
    """Fetch notes for the contact and up to 2 top colleagues.

    Bot-noise filter, live hiring signal parsing, and sensitive items detection
    are all applied here.

    Returns (story_notes: list[str], live_hiring_signals: list[dict], sensitive_items: list[str]).
    """
    # Determine colleague IDs to fetch (D-11)
    contact_first = (contact_props.get("firstname") or "").strip().lower()
    contact_last = (contact_props.get("lastname") or "").strip().lower()

    colleagues = [
        c for c in all_company_contacts
        if not (
            (c.get("firstname") or "").strip().lower() == contact_first
            and (c.get("lastname") or "").strip().lower() == contact_last
        )
        and c.get("num_contacted_notes", 0) >= 1
    ]
    # Sort by num_contacted_notes descending, take top 2
    colleagues.sort(key=lambda c: c.get("num_contacted_notes", 0), reverse=True)
    top_colleagues = colleagues[:2]

    person_ids = [contact_id] + [c["_id"] for c in top_colleagues]

    # Collect all NOTE engagement bodies
    raw_bodies = []
    for pid in person_ids:
        engagements = _fetch_engagements_paged(pid)
        for eng in engagements:
            if eng.get("engagement", {}).get("type") == "NOTE":
                body = eng.get("metadata", {}).get("body") or ""
                if body:
                    raw_bodies.append(body)

    story_notes = []
    live_hiring_signals = []
    sensitive_items = []

    for body in raw_bodies:
        first_80 = body[:80]

        # Check if it is bot noise
        is_bot_noise = any(prefix in first_80 for prefix in BOT_NOISE_PREFIXES)
        is_job_ad = any(prefix in first_80 for prefix in JOB_AD_PREFIXES)

        if is_bot_noise:
            if is_job_ad:
                signal = _parse_hiring_signal(body)
                live_hiring_signals.append(signal)
            # All bot-noise notes are discarded from story_notes
            continue

        # Non-bot-noise: store as story note (capped at 5000 chars)
        truncated = safe_truncate(body, 5000)
        story_notes.append(truncated)

        # Sensitive items detection (D-08)
        if _is_sensitive(body):
            sensitive_items.append(safe_truncate(body, 300))

    return (story_notes, live_hiring_signals, sensitive_items)


def fetch_handover(contact_id: str) -> dict:
    """Resolve the handover owner — the person who last contacted this contact
    by CALL or outbound EMAIL (whichever is more recent).

    Returns a dict with keys: first_name, last_contact_date, method, is_active.
    Returns None if zero CALL and zero outbound EMAIL engagements exist.
    """
    engagements = _fetch_engagements_paged(contact_id)

    best_call = None       # (timestamp, owner_id)
    best_email = None      # (timestamp, owner_id)

    for eng in engagements:
        engagement = eng.get("engagement", {})
        metadata = eng.get("metadata", {})
        eng_type = engagement.get("type")
        timestamp = engagement.get("timestamp", 0)
        owner_id = engagement.get("ownerId")

        if eng_type == "CALL":
            if best_call is None or timestamp > best_call[0]:
                best_call = (timestamp, owner_id)

        elif eng_type == "EMAIL":
            direction = metadata.get("direction")
            if direction == "EMAIL":  # outbound only
                if best_email is None or timestamp > best_email[0]:
                    best_email = (timestamp, owner_id)

    if best_call is None and best_email is None:
        return None

    # Pick the later of the two
    if best_call is None:
        winning_ts, winning_owner_id, method = best_email[0], best_email[1], "email"
    elif best_email is None:
        winning_ts, winning_owner_id, method = best_call[0], best_call[1], "call"
    elif best_call[0] >= best_email[0]:
        winning_ts, winning_owner_id, method = best_call[0], best_call[1], "call"
    else:
        winning_ts, winning_owner_id, method = best_email[0], best_email[1], "email"

    @retry(**HS_RETRY_KWARGS)
    def _get_owner(owner_id):
        return client.crm.owners.owners_api.get_by_id(owner_id)

    owner = _get_owner(winning_owner_id)
    last_contact_date = datetime.utcfromtimestamp(winning_ts / 1000).strftime("%Y-%m-%d")

    return {
        "first_name": owner.first_name,
        "last_contact_date": last_contact_date,
        "method": method,
        "is_active": bool(owner.active),
        "owner_id": str(winning_owner_id) if winning_owner_id is not None else None,
    }


# ---------------------------------------------------------------------------
# Resolution functions (added in Plan 02-03)
# ---------------------------------------------------------------------------


def resolve_geo(company_props: dict, contact_props: dict) -> str:
    """Resolve the geographic market for this contact using a 3-step ladder (D-06).

    Step 1: Match company.country string (case-insensitive).
    Step 2: Match contact.phone prefix (after stripping spaces and dashes).
    Step 3: Return "UNRESOLVED" and log a warning to stderr.

    Always returns a non-empty string — never raises.
    """
    # Step 1 — company country string match
    country = (company_props.get("country") or "").strip().lower()
    AU_COUNTRIES = {"australia", "au"}
    NZ_COUNTRIES = {"new zealand", "nz"}
    US_COUNTRIES = {"united states", "us", "usa", "united states of america"}
    UK_COUNTRIES = {"united kingdom", "uk", "gb", "great britain"}

    if country in AU_COUNTRIES:
        return "AU"
    if country in NZ_COUNTRIES:
        return "NZ"
    if country in US_COUNTRIES:
        return "US"
    if country in UK_COUNTRIES:
        return "UK"

    # Step 2 — phone prefix (strip spaces and dashes first)
    phone = (contact_props.get("phone") or "").replace(" ", "").replace("-", "")
    if phone.startswith("+61"):
        return "AU"
    if phone.startswith("+64"):
        return "NZ"
    if phone.startswith("+1"):
        return "US"
    if phone.startswith("+44"):
        return "UK"

    # Step 3 — unresolved; Phase 3 holds these records
    print(
        f"GEO UNRESOLVED for contact {contact_props.get('email', '?')}",
        file=sys.stderr,
    )
    return "UNRESOLVED"


def check_departure(story_notes: list, contact_props: dict) -> bool:
    """Return True if any story note suggests the contact has left their company (D-14).

    Searches for "has left", "no longer with", "moved on from" within 30 chars of the
    contact's first or last name (case-insensitive). Returns False if both names are
    empty (cannot perform meaningful check).
    """
    firstname = (contact_props.get("firstname") or "").strip()
    lastname = (contact_props.get("lastname") or "").strip()

    if not firstname and not lastname:
        return False

    name_pattern = "|".join(
        filter(None, [re.escape(firstname), re.escape(lastname)])
    )

    departure_phrases = ["has left", "no longer with", "moved on from"]

    for body in story_notes:
        if not body:
            continue
        for phrase in departure_phrases:
            pattern = (
                rf"(?i)(?:{name_pattern}).{{0,30}}(?:{re.escape(phrase)})"
                rf"|(?:{re.escape(phrase)}).{{0,30}}(?:{name_pattern})"
            )
            if re.search(pattern, body):
                return True

    return False


def is_only_contact_check(contact_props: dict, all_company_contacts: list) -> bool:
    """Return True if the contact is the sole person with num_contacted_notes >= 1 (FETCH-10).

    Fallback: if all_company_contacts is empty, return True (conservative — Phase 3
    brief will say "only contact", which is a safe overstatement per D-14).
    """
    contacted = [
        c for c in all_company_contacts
        if int(c.get("num_contacted_notes") or 0) >= 1
    ]

    if len(contacted) == 0:
        return True  # fallback: no association data returned

    if len(contacted) == 1:
        c = contacted[0]
        same_first = (c.get("firstname") or "").lower() == (contact_props.get("firstname") or "").lower()
        same_last = (c.get("lastname") or "").lower() == (contact_props.get("lastname") or "").lower()
        return same_first and same_last

    return False  # multiple contacted people → not the only contact


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        raise IndexError("Usage: python fetch_record.py <contact_id>")

    contact_id = sys.argv[1]

    # DLQ sentinel — written at startup before any fetch calls (D-15)
    write_dlq(contact_id, "", "startup", "sentinel", 0)

    failed_step = "unknown"
    try:
        failed_step = "fetch_contact"
        contact_props = fetch_contact(contact_id)

        failed_step = "fetch_company"
        company_id, company_props = fetch_company(contact_id)

        failed_step = "fetch_all_company_contacts"
        all_company_contacts = fetch_all_company_contacts(company_id)

        failed_step = "fetch_deals"
        deals = fetch_deals(company_id)

        failed_step = "fetch_notes"
        story_notes, live_hiring_signals, sensitive_items = fetch_notes(
            contact_id, contact_props, all_company_contacts
        )

        failed_step = "fetch_handover"
        handover = fetch_handover(contact_id)

        # Resolution functions (Plan 02-03)
        failed_step = "resolve_geo"
        geo = resolve_geo(company_props, contact_props)

        failed_step = "check_departure"
        departure_flagged = check_departure(story_notes, contact_props)

        failed_step = "is_only_contact"
        is_only_contact = is_only_contact_check(contact_props, all_company_contacts)

        # Assemble the D-13 record (all 12 keys, in spec order)
        record = {
            "contact_props": contact_props,
            "company_id": company_id,
            "company_props": company_props,
            "all_company_contacts": all_company_contacts,
            "deals": deals,
            "story_notes": story_notes,
            "live_hiring_signals": live_hiring_signals,
            "handover": handover,
            "geo": geo,
            "is_only_contact": is_only_contact,
            "sensitive_items": sensitive_items,
            "departure_flagged": departure_flagged,
        }

        # Write output — only reached if all fetch + resolution calls succeeded (D-15, T-02-10)
        out_path = os.path.join(RUNNER_TEMP, f"contact_{contact_id}.json")
        failed_step = "write_output"
        with open(out_path, "w") as f:
            json.dump(record, f, indent=2, default=str)
        print(f"Written {out_path}")

    except Exception as e:
        write_dlq(contact_id, "", failed_step, str(e), 0)
        sys.exit(1)


if __name__ == "__main__":
    main()
