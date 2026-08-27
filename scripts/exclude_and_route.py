"""exclude_and_route.py — Exclusion, routing, and brief assembly for Lane A re-engagement pipeline.

Usage:
    python scripts/exclude_and_route.py

Reads $RUNNER_TEMP/contact_ids.json, applies exclusion filters E1–E6 and geo-hold to each
contact_{id}.json, writes $RUNNER_TEMP/exclusion_report.json for excluded/held contacts,
and builds $RUNNER_TEMP/brief_{id}.json for passing contacts (routing and brief assembly
are completed in Plan 03-02).
"""

import json
import os
import sys
from datetime import datetime, timedelta

from config.vertical_routing import route, ROUTING_TABLE
from config.close_bank import assign_close
from utils import write_dlq

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECENT_DAYS = 14
HUBSPOT_API_KEY = os.environ["HUBSPOT_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")

# ---------------------------------------------------------------------------
# Record loader
# ---------------------------------------------------------------------------


def load_record(contact_id: str) -> dict:
    path = os.path.join(RUNNER_TEMP, f"contact_{contact_id}.json")
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Date helper
# ---------------------------------------------------------------------------


def _parse_date(date_str: str):
    """Parse HubSpot date string to naive UTC datetime. Returns None if unparseable."""
    if not date_str:
        return None
    for fmt in ["%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Exclusion filter functions
# Each returns (excluded: bool, filter_code: str, reason: str).
# filter_code is empty string when excluded=False.
# ---------------------------------------------------------------------------


def check_e1(record: dict) -> tuple:
    """E1: last-activity owner ID == current hubspot_owner_id → skip (EXCL-01)."""
    handover = record.get("handover")
    if not handover:
        return (False, "", "")
    owner_id = str(handover.get("owner_id") or "")
    current_owner = str(record["contact_props"].get("hubspot_owner_id") or "")
    if owner_id and current_owner and owner_id == current_owner:
        return (True, "E1_OWNER_UNCHANGED",
                "Last-activity owner is the same as the current owner; handover email would be false.")
    return (False, "", "")


def check_e2(record: dict) -> tuple:
    """E2: last-activity owner still isActive but not current owner → hold for JP (EXCL-02)."""
    handover = record.get("handover")
    if not handover:
        return (False, "", "")
    owner_id = str(handover.get("owner_id") or "")
    current_owner = str(record["contact_props"].get("hubspot_owner_id") or "")
    is_active = bool(handover.get("is_active", False))
    if owner_id and current_owner and owner_id != current_owner and is_active:
        return (True, "E2_PREV_OWNER_STILL_ACTIVE",
                f"Previous owner (ID {owner_id}) is still active; 'I've taken over from X' would read as false.")
    return (False, "", "")


def check_e3(record: dict) -> tuple:
    """E3: any engagement in the last 14 days → hold (EXCL-03)."""
    cutoff = datetime.utcnow() - timedelta(days=RECENT_DAYS)
    # Primary: notes_last_contacted HubSpot system property
    last_contacted = _parse_date(record["contact_props"].get("notes_last_contacted") or "")
    if last_contacted and last_contacted > cutoff:
        return (True, "E3_RECENT_ACTIVITY",
                f"Last contacted {record['contact_props'].get('notes_last_contacted')} — within {RECENT_DAYS} days.")
    # Fallback: handover last_contact_date (CALL or outbound EMAIL)
    handover = record.get("handover")
    if handover:
        last_date = _parse_date(handover.get("last_contact_date") or "")
        if last_date and last_date > cutoff:
            return (True, "E3_RECENT_ACTIVITY",
                    f"Last handover engagement {handover.get('last_contact_date')} — within {RECENT_DAYS} days.")
    return (False, "", "")


def check_e4(contact_id: str, record: dict, seen: dict) -> tuple:
    """E4: duplicate contact (same email + company_id on the list) → report for merge (EXCL-04).

    seen dict maps email:company_id → first contact_id seen. Mutated in place.
    """
    email = (record["contact_props"].get("email") or "").lower().strip()
    company_id = record.get("company_id") or ""
    if not email or not company_id:
        return (False, "", "")
    key = f"{email}:{company_id}"
    if key in seen:
        first_id = seen[key]
        return (True, f"E4_DUPLICATE_OF_{first_id}",
                f"Duplicate contact record — same email and company as contact ID {first_id}. Report for merge.")
    seen[key] = contact_id
    return (False, "", "")


def check_e5(record: dict) -> tuple:
    """E5: no CALL and no outbound EMAIL history → skip (EXCL-05)."""
    if record.get("handover") is None:
        return (True, "E5_NO_ENGAGEMENT_HISTORY",
                "Zero CALL and zero outbound EMAIL engagements — no relationship to hand over.")
    return (False, "", "")


def check_e6(record: dict) -> tuple:
    """E6: unsubscribed, bounced, or invalid email → skip (EXCL-06)."""
    props = record["contact_props"]
    if not props.get("email"):
        return (True, "E6_INVALID_EMAIL", "Contact has no email address.")
    if props.get("hs_email_optout"):
        return (True, "E6_UNSUBSCRIBED", "Contact is unsubscribed (hs_email_optout=true).")
    if props.get("hs_email_bounce"):
        return (True, "E6_BOUNCED", "Contact email has bounced (hs_email_bounce=true).")
    return (False, "", "")


def check_geo_hold(record: dict) -> tuple:
    """Pre-filter: geo=UNRESOLVED → data-error hold (spec §3.6, STATE.md decision)."""
    if record.get("geo") == "UNRESOLVED":
        email = record["contact_props"].get("email", "?")
        return (True, "GEO_UNRESOLVED",
                f"Geo could not be resolved for {email}. Hold for data correction before send.")
    return (False, "", "")


# ---------------------------------------------------------------------------
# Main filter stage
# ---------------------------------------------------------------------------


def main():
    ids_path = os.path.join(RUNNER_TEMP, "contact_ids.json")
    with open(ids_path) as f:
        contact_ids = json.load(f)

    # DLQ sentinel at startup (ERR-02)
    write_dlq("batch", "", "startup", "sentinel", 0)

    # Load all records; log but don't abort on individual load failures
    records = {}
    load_errors = []
    for cid in contact_ids:
        try:
            records[cid] = load_record(cid)
        except Exception as e:
            load_errors.append({"contact_id": cid, "filter_code": "LOAD_ERROR",
                                 "reason": f"Failed to load contact_{cid}.json: {e}"})

    # E4 dedup state — shared across all contacts
    seen_emails: dict = {}

    excluded = list(load_errors)
    passing = []

    for cid, record in records.items():
        # Apply geo hold first (not a spec filter code, but required pre-filter)
        geo_excluded, geo_code, geo_reason = check_geo_hold(record)
        if geo_excluded:
            excluded.append({"contact_id": cid, "filter_code": geo_code, "reason": geo_reason})
            continue

        # Apply E1–E6 in order; stop at first match
        filters = [
            check_e1(record),
            check_e2(record),
            check_e3(record),
            check_e4(cid, record, seen_emails),
            check_e5(record),
            check_e6(record),
        ]
        hit = next((f for f in filters if f[0]), None)
        if hit:
            _, code, reason = hit
            excluded.append({"contact_id": cid, "filter_code": code, "reason": reason})
        else:
            passing.append(cid)

    # Write exclusion_report.json (always written, even if empty) (EXCL-07)
    report_path = os.path.join(RUNNER_TEMP, "exclusion_report.json")
    with open(report_path, "w") as f:
        json.dump(excluded, f, indent=2)
    print(f"Exclusion report: {len(excluded)} excluded, {len(passing)} passing. Written {report_path}")

    # Plan 03-02 will add routing and brief assembly here for the `passing` list.
    return passing, records


if __name__ == "__main__":
    main()
