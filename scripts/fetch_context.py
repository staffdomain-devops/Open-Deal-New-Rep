"""fetch_context.py — Per-contact raw HubSpot context assembly.

Usage:
    python scripts/fetch_context.py <contact_id>
    (or set INPUT_CONTACT_ID in the environment)

Writes $RUNNER_TEMP/context_{id}.json with everything Agent 1 needs to reason
about the record: contact/company properties, all company contacts, all
company deals, story notes + live hiring signals, resolved handover, geo,
only-contact flag, departure flag, and duplicate-name signals. This script
makes no judgment calls — it fetches and does only mechanical filtering
(junk deal names, bot-noise note prefixes). Everything else (sensitivity
detection, exclusion verdicts, colleague selection) is Agent 1's job.
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
# Fetch functions
# ---------------------------------------------------------------------------


@retry(**HS_RETRY_KWARGS)
def fetch_contact(contact_id: str) -> dict:
    """Fetch contact properties for the given contact ID."""
    result = client.crm.contacts.basic_api.get_by_id(
        contact_id, properties=CONTACT_PROPS
    )
    props = result.properties or {}
    return {p: props.get(p) for p in CONTACT_PROPS}


@retry(**HS_RETRY_KWARGS)
def fetch_company(contact_id: str) -> tuple:
    """Fetch the company associated with a contact and its properties.

    Returns (company_id: str, company_props: dict). ("", {}) if no association.
    """
    assoc = client.crm.associations.v4.basic_api.get_page("contacts", contact_id, "companies")
    results = assoc.results or []
    if not results:
        return ("", {})
    company_id = str(results[0].to_object_id)
    company = client.crm.companies.basic_api.get_by_id(
        company_id, properties=COMPANY_PROPS
    )
    props = company.properties or {}
    return (company_id, {p: props.get(p) for p in COMPANY_PROPS})


@retry(**HS_RETRY_KWARGS)
def fetch_all_company_contacts(company_id: str) -> list:
    """Fetch all contacts associated with the given company."""
    if not company_id:
        return []

    assoc = client.crm.associations.v4.basic_api.get_page("companies", company_id, "contacts")
    contact_ids = [r.to_object_id for r in (assoc.results or [])]
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
    """Fetch all junk-filtered deals associated with the given company."""
    if not company_id:
        return []

    assoc = client.crm.associations.v4.basic_api.get_page("companies", company_id, "deals")
    deal_ids = [r.to_object_id for r in (assoc.results or [])]
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
    """Fetch all engagements for a contact via the v1 legacy endpoint."""

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
    """Best-effort extraction of role + month from a job-ad note body."""
    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
        "Jan", "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]

    role = None
    role_match = re.search(
        r"(?:for|opportunity:)\s+([A-Z][^(\n]{2,60}?)(?:\s*[\(\n]|$)", body
    )
    if role_match:
        role = role_match.group(1).strip().rstrip(".,")

    month = None
    for m in month_names:
        if m in body:
            month = m
            break

    if not role:
        for line in body.splitlines():
            line = line.strip()
            if line:
                role = line
                break

    return {"role": role or "", "month": month or ""}


def fetch_notes(
    contact_id: str, contact_props: dict, all_company_contacts: list
) -> tuple:
    """Fetch notes for the contact and up to 2 top colleagues.

    Only mechanical bot-noise filtering and live-hiring-signal parsing happen
    here. Sensitive/INTERNAL-content detection is Agent 1's job, not regex's.

    Returns (story_notes: list[str], live_hiring_signals: list[dict]).
    """
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
    colleagues.sort(key=lambda c: c.get("num_contacted_notes", 0), reverse=True)
    top_colleagues = colleagues[:2]

    person_ids = [contact_id] + [c["_id"] for c in top_colleagues]

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

    for body in raw_bodies:
        first_80 = body[:80]

        is_bot_noise = any(prefix in first_80 for prefix in BOT_NOISE_PREFIXES)
        is_job_ad = any(prefix in first_80 for prefix in JOB_AD_PREFIXES)

        if is_bot_noise:
            if is_job_ad:
                signal = _parse_hiring_signal(body)
                live_hiring_signals.append(signal)
            continue

        story_notes.append(safe_truncate(body, 5000))

    return (story_notes, live_hiring_signals)


def fetch_handover(contact_id: str) -> dict:
    """Resolve the handover owner — the person who last contacted this contact
    by CALL or outbound EMAIL (whichever is more recent).

    Returns a dict with keys: first_name, last_contact_date, method, is_active,
    owner_id. Returns None if zero CALL and zero outbound EMAIL engagements exist.
    """
    engagements = _fetch_engagements_paged(contact_id)

    best_call = None
    best_email = None

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
            if direction == "EMAIL":
                if best_email is None or timestamp > best_email[0]:
                    best_email = (timestamp, owner_id)

    if best_call is None and best_email is None:
        return None

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
        "is_active": not bool(getattr(owner, "archived", False)),
        "owner_id": str(winning_owner_id) if winning_owner_id is not None else None,
    }


# ---------------------------------------------------------------------------
# Resolution / signal functions
# ---------------------------------------------------------------------------


def resolve_geo(company_props: dict, contact_props: dict) -> str:
    """Resolve the geographic market for this contact using a 3-step ladder."""
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

    phone = (contact_props.get("phone") or "").replace(" ", "").replace("-", "")
    if phone.startswith("+61"):
        return "AU"
    if phone.startswith("+64"):
        return "NZ"
    if phone.startswith("+1"):
        return "US"
    if phone.startswith("+44"):
        return "UK"

    print(
        f"GEO UNRESOLVED for contact {contact_props.get('email', '?')}",
        file=sys.stderr,
    )
    return "UNRESOLVED"


def check_departure(story_notes: list, contact_props: dict) -> bool:
    """Return True if any story note suggests the contact has left their company."""
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
    """Return True if the contact is the sole person with num_contacted_notes >= 1."""
    contacted = [
        c for c in all_company_contacts
        if int(c.get("num_contacted_notes") or 0) >= 1
    ]

    if len(contacted) == 0:
        return True

    if len(contacted) == 1:
        c = contacted[0]
        same_first = (c.get("firstname") or "").lower() == (contact_props.get("firstname") or "").lower()
        same_last = (c.get("lastname") or "").lower() == (contact_props.get("lastname") or "").lower()
        return same_first and same_last

    return False


def find_possible_duplicates(
    contact_id: str, contact_props: dict, all_company_contacts: list
) -> list:
    """Return sibling contact IDs at the same company sharing this contact's
    normalised first+last name (E4 duplicate-record signal).

    This replaces the old batch-wide "seen" dict — since all company contacts
    are already fetched per record, an exact-name collision at the same
    company is detectable without any cross-run state.
    """
    firstname = (contact_props.get("firstname") or "").strip().lower()
    lastname = (contact_props.get("lastname") or "").strip().lower()
    if not firstname and not lastname:
        return []

    duplicates = []
    for c in all_company_contacts:
        if c.get("_id") == str(contact_id):
            continue
        if (
            (c.get("firstname") or "").strip().lower() == firstname
            and (c.get("lastname") or "").strip().lower() == lastname
        ):
            duplicates.append(c["_id"])
    return duplicates


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _fetch_one(contact_id: str) -> None:
    """Fetch and write all context data for a single contact.

    Raises RuntimeError("<step>: <original error>") on any failure, so callers
    can report which step failed without needing a full traceback.
    """
    step = "fetch_contact"
    try:
        contact_props = fetch_contact(contact_id)

        step = "fetch_company"
        company_id, company_props = fetch_company(contact_id)

        step = "fetch_all_company_contacts"
        all_company_contacts = fetch_all_company_contacts(company_id)

        step = "fetch_deals"
        deals = fetch_deals(company_id)

        step = "fetch_notes"
        story_notes, live_hiring_signals = fetch_notes(
            contact_id, contact_props, all_company_contacts
        )

        step = "fetch_handover"
        handover = fetch_handover(contact_id)

        step = "resolve_geo"
        geo = resolve_geo(company_props, contact_props)

        step = "check_departure"
        departure_flagged = check_departure(story_notes, contact_props)

        step = "is_only_contact"
        is_only_contact = is_only_contact_check(contact_props, all_company_contacts)

        step = "find_possible_duplicates"
        possible_duplicates = find_possible_duplicates(contact_id, contact_props, all_company_contacts)
    except Exception as exc:
        raise RuntimeError(f"{step}: {exc}") from exc

    record = {
        "contact_id": str(contact_id),
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
        "departure_flagged": departure_flagged,
        "possible_duplicates": possible_duplicates,
    }

    out_path = os.path.join(RUNNER_TEMP, f"context_{contact_id}.json")
    with open(out_path, "w") as f:
        json.dump(record, f, indent=2, default=str)
    print(f"Written {out_path}")


def main():
    contact_id = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("INPUT_CONTACT_ID")
    if not contact_id:
        print("ERROR: contact_id not provided (argv[1] or INPUT_CONTACT_ID).", file=sys.stderr)
        sys.exit(1)

    write_dlq(contact_id, "", "startup", "sentinel", 0)
    try:
        _fetch_one(contact_id)
    except Exception as e:
        write_dlq(contact_id, "", "fetch_context", str(e), 0)
        print(f"ERROR {contact_id}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
