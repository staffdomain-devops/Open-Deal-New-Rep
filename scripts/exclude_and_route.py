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
# Routing helpers
# ---------------------------------------------------------------------------


def route_contact(record: dict) -> tuple:
    """Return (VerticalRoute, industry_match_strength) for a contact record.

    industry_match_strength is "exact" when a named row (tokens non-empty) matched;
    "partial" when the default row fired (empty industry or no token match).
    """
    industry = record["contact_props"].get("industry") or ""
    vr = route(industry)

    normalised = industry.lower().strip() if industry else ""
    matched_named = False
    for row in ROUTING_TABLE:
        if not row["tokens"]:
            continue
        if any(token in normalised for token in row["tokens"]):
            matched_named = True
            break
    industry_match_strength = "exact" if matched_named else "partial"

    return (vr, industry_match_strength)


def _get_colleagues(contact_props: dict, all_company_contacts: list) -> list:
    """Return up to 2 colleagues with num_contacted_notes >= 1, excluding the contact themselves."""
    contact_first = (contact_props.get("firstname") or "").strip().lower()
    contact_last = (contact_props.get("lastname") or "").strip().lower()

    colleagues = [
        c for c in all_company_contacts
        if (
            int(c.get("num_contacted_notes") or 0) >= 1
            and not (
                (c.get("firstname") or "").strip().lower() == contact_first
                and (c.get("lastname") or "").strip().lower() == contact_last
            )
        )
    ]
    colleagues.sort(key=lambda c: int(c.get("num_contacted_notes") or 0), reverse=True)
    return colleagues[:2]


# ---------------------------------------------------------------------------
# Brief assembly
# ---------------------------------------------------------------------------

GEO_DISPLAY = {
    "AU": "Australia",
    "NZ": "New Zealand",
    "US": "United States. Nothing in the copy may assume Australia.",
    "UK": "United Kingdom. Nothing in the copy may assume Australia.",
}


def build_brief(contact_id: str, record: dict, vr, close_option: int, close_text: str) -> str:
    """Assemble the §3.7 research brief as a plain-text string. Field order is spec-locked."""
    props = record["contact_props"]
    company_props = record["company_props"]
    handover = record["handover"]
    deals = record["deals"]
    story_notes = record["story_notes"]
    live_signals = record["live_hiring_signals"]
    sensitive = record["sensitive_items"]
    is_only = record["is_only_contact"]
    all_contacts = record["all_company_contacts"]
    geo = record.get("geo", "")

    firstname = props.get("firstname") or ""
    lastname = props.get("lastname") or ""
    jobtitle = props.get("jobtitle") or "not recorded"
    company_name = company_props.get("name") or props.get("company") or "not recorded"
    industry = props.get("industry") or "not recorded"
    geo_display = GEO_DISPLAY.get(geo, geo or "not recorded")

    lines = []

    lines.append(f"CONTACT: {firstname} {lastname}".strip())
    lines.append(f"JOB TITLE: {jobtitle}")
    lines.append(f"COMPANY: {company_name}")
    lines.append(f"INDUSTRY: {industry}")
    lines.append(f"COUNTRY: {geo_display}")
    lines.append("")

    h_name = handover["first_name"] if handover else "Unknown"
    h_method = handover.get("method", "unknown") if handover else "unknown"
    h_date = handover.get("last_contact_date", "unknown") if handover else "unknown"
    lines.append(f"HANDOVER: The last person to contact them was {h_name} ({h_method}, {h_date}).")
    lines.append(f"{h_name} has left the business. Open email 1 by saying you have recently taken over the account from {h_name}.")
    lines.append("")

    if is_only:
        lines.append("The recipient is the only contact on this account. Do not reference colleagues.")
    else:
        colleagues = _get_colleagues(props, all_contacts)
        if colleagues:
            colleague_parts = [
                f"{c.get('firstname', '')} ({c.get('jobtitle', 'role unknown')})"
                for c in colleagues
            ]
            lines.append(f"COLLEAGUES WE ALSO DEALT WITH: {', '.join(colleague_parts)}")
            lines.append("  — name one or two of them by first name in email 1.")
        else:
            lines.append("COLLEAGUES WE ALSO DEALT WITH: none on record.")
    lines.append("")

    if not deals:
        lines.append("DEAL HISTORY: No previous deal on record. Do not invent one. Email 1 is relationship-only;")
        lines.append("email 3 leads with an industry observation, not a role follow-up.")
    else:
        deal_parts = []
        for d in deals:
            name = d.get("dealname") or "unnamed deal"
            stage = d.get("dealstage") or "unknown stage"
            created = (d.get("createdate") or "")[:10]
            closed = (d.get("closedate") or "")[:10]
            part = f"{name} ({stage}"
            if created:
                part += f", created {created}"
            if closed:
                part += f", closed {closed}"
            part += ")"
            deal_parts.append(part)
        lines.append("DEAL HISTORY: " + "; ".join(deal_parts))
    lines.append("")

    lines.append("WHAT THE NOTES SAY:")
    notes_to_use = story_notes[:5]
    if notes_to_use:
        for note in notes_to_use:
            first_line = note.strip().splitlines()[0] if note.strip() else note.strip()
            lines.append(f"- {first_line[:300]}")
    else:
        lines.append("- No notes on record.")
    lines.append("")

    if sensitive:
        lines.append("INTERNAL - NEVER REFERENCE:")
        for item in sensitive:
            lines.append(f"  {item[:300]}")
        lines.append("")

    if live_signals:
        signal_parts = [
            f"{s.get('role', 'unknown role')} ({s.get('month', 'unknown month')})"
            for s in live_signals
        ]
        lines.append(f"LIVE HIRING SIGNALS (public job ads): {', '.join(signal_parts)}")
        lines.append("")

    lines.append(f"CASE STUDY FOR EMAIL 2: {vr.case_study}. Do not describe its contents.")
    lines.append(f"EMAIL 3 INLINE PAGE: {vr.email3_url}")
    lines.append(f"EMAIL 4 INLINE PAGE: {vr.email4_url}")
    lines.append(f"EMAIL 1 CLOSE: Use close option {close_option} from the close bank.")

    return "\n".join(lines)


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

    # Routing and brief assembly for passing contacts
    briefs_written = 0
    for cid in passing:
        record = records[cid]
        try:
            vr, industry_match_strength = route_contact(record)

            touches = int(record["contact_props"].get("num_contacted_notes") or 0)
            jobtitle = record["contact_props"].get("jobtitle") or ""
            close_option, close_text = assign_close(touches, jobtitle, industry_match_strength)

            brief_text = build_brief(cid, record, vr, close_option, close_text)

            brief_payload = {
                "contact_id": cid,
                "brief_text": brief_text,
                "case_study": vr.case_study,
                "email3_url": vr.email3_url,
                "email4_url": vr.email4_url,
                "close_option": close_option,
                "close_text": close_text,
            }
            brief_path = os.path.join(RUNNER_TEMP, f"brief_{cid}.json")
            with open(brief_path, "w") as f:
                json.dump(brief_payload, f, indent=2)
            briefs_written += 1

        except Exception as e:
            write_dlq(cid, record["contact_props"].get("email", ""), "routing_brief_assembly", str(e), 0)
            print(f"ERROR routing/brief for {cid}: {e}", file=sys.stderr)

    print(f"Briefs written: {briefs_written}/{len(passing)}")

    return passing, records


if __name__ == "__main__":
    main()
