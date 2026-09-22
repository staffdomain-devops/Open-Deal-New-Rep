"""write_hubspot.py — Write assembled contact properties to HubSpot (single contact).

Reads assembled_{id}.json from RUNNER_TEMP, validates property schema, guards
against unresolved bracket placeholders, writes all 12 contact properties,
then creates and pins the contact note.

Usage:
    python scripts/write_hubspot.py <contact_id>
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

import hubspot
from hubspot.crm.contacts import SimplePublicObjectInput
from hubspot.crm.objects.notes import (
    AssociationSpec,
    PublicAssociationsForObject,
    PublicObjectId,
    SimplePublicObjectInputForCreate,
)
from tenacity import retry

from utils import write_dlq, HS_RETRY_KWARGS

HUBSPOT_API_KEY = os.environ["HUBSPOT_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")

# Ordered list of (hubspot_property_name, (top_key, field)) for all 12 properties.
PROPERTY_MAP = [
    ("subject_1",   ("e1", "subject")),
    ("email_1",     ("e1", "body")),
    ("subject_2",   ("e2", "subject")),
    ("email_2",     ("e2", "body")),
    ("subject_3",   ("e3", "subject")),
    ("email_3",     ("e3", "body")),
    ("subject_4",   ("e4", "subject")),
    ("email_4",     ("e4", "body")),
    ("subject_5",   ("e5", "subject")),
    ("email_5",     ("e5", "body")),
    ("task_note_1", ("call1", "body")),
    ("task_note_2", ("call2", "body")),
]

# Frozenset of the 10 email property names only; task_note_1 and task_note_2
# are intentionally excluded so the bracket guard never scans call briefings.
EMAIL_PROP_NAMES = frozenset(
    prop_name
    for prop_name, _ in PROPERTY_MAP
    if prop_name.startswith("email_") or prop_name.startswith("subject_")
)

# Only properties that actually carry \n-separated paragraphs/labelled lines
# need to be 'textarea' (multi-line text) — a single-line 'text' property
# silently strips newlines, which is a real bug for these (the recipient
# gets one wall-of-text block instead of paragraphs). subject_1-5 are
# deliberately excluded: lint caps them at 45 chars, one line, so they never
# carry a line break, and this portal already has them provisioned as plain
# single-line text — other pipelines writing to them successfully confirms
# that's the correct type, not a misconfiguration to "fix" in HubSpot.
TEXTAREA_PROPERTY_NAMES = frozenset(
    prop_name
    for prop_name, _ in PROPERTY_MAP
    if prop_name.startswith("email_") or prop_name.startswith("task_note_")
)

# Intentional bracket patterns that must NOT trigger the bracket guard:
#   [Rep first name]  — sign-off placeholder (lint H07 requires it)
#   [Insert ... ]     — link placeholders appended by assemble_bodies.py
_ALLOWED_BRACKETS_RE = re.compile(r"\[Rep first name\]|\[Insert [^\]]+\]")


def _check_property_schema(client) -> None:
    """Verify the multi-line body/task-note properties are 'textarea'.

    subject_1-5 are intentionally NOT checked here — see
    TEXTAREA_PROPERTY_NAMES for why a single-line 'text' property is correct
    for them, not a bug to flag.
    """
    for prop_name in TEXTAREA_PROPERTY_NAMES:
        resp = client.crm.properties.core_api.get_by_name("contacts", prop_name)
        actual = resp.field_type
        if actual != "textarea":
            print(
                f"ERROR: HubSpot property '{prop_name}' has field_type='{actual}', "
                f"expected 'textarea' (it carries multi-paragraph content that would "
                f"otherwise silently lose its line breaks). Fix in HubSpot before "
                f"running write-back."
            )
            sys.exit(1)
    print(
        f"Property schema check passed: all {len(TEXTAREA_PROPERTY_NAMES)} "
        f"multi-line properties are textarea (subject_1-5 are not checked; "
        f"single-line text is correct for them)."
    )


def _bracket_guard(assembled: dict) -> None:
    """Raise ValueError if any email property value contains an unresolved '[' placeholder."""
    for prop_name, (top_key, field) in PROPERTY_MAP:
        if prop_name not in EMAIL_PROP_NAMES:
            continue
        section = assembled.get(top_key)
        if section is None:
            raise ValueError(f"Bracket guard: missing top-level key '{top_key}' in assembled JSON")
        value = section.get(field)
        if value is None:
            raise ValueError(f"Bracket guard: missing field '{field}' under '{top_key}'")
        if "[" in _ALLOWED_BRACKETS_RE.sub("", value):
            raise ValueError(
                f"Bracket guard: unresolved placeholder in '{prop_name}' for contact "
                f"{assembled.get('contact_id')}. Value starts: {value[:80]!r}"
            )


def _build_properties(assembled: dict) -> dict:
    return {
        prop_name: assembled[top_key][field]
        for prop_name, (top_key, field) in PROPERTY_MAP
    }


@retry(**HS_RETRY_KWARGS)
def _write_properties(client, contact_id: str, properties: dict) -> None:
    client.crm.contacts.basic_api.update(
        contact_id=contact_id,
        simple_public_object_input=SimplePublicObjectInput(properties=properties),
    )


@retry(**HS_RETRY_KWARGS)
def _create_note_object(client, body: str, contact_id: str) -> str:
    """Create the note and associate it to the contact in one atomic call —
    avoids a second API round-trip that could leave an orphaned, unassociated
    note if it failed independently."""
    note_input = SimplePublicObjectInputForCreate(
        properties={
            "hs_note_body": body,
            "hs_timestamp": datetime.now(timezone.utc).isoformat(),
        },
        associations=[
            PublicAssociationsForObject(
                to=PublicObjectId(id=str(contact_id)),
                types=[
                    AssociationSpec(
                        association_category="HUBSPOT_DEFINED", association_type_id=202
                    )
                ],
            )
        ],
    )
    response = client.crm.objects.notes.basic_api.create(
        simple_public_object_input_for_create=note_input
    )
    return str(response.id)


def _pin_note(contact_id: str, note_id: str, client) -> bool:
    """Attempt to pin a note on the contact via hs_pinned_engagement_id.

    Not retried — pin failure is a soft fallback, not a retriable error. On
    any Exception, appends to manual_pin_list.json and returns False.
    """
    try:
        client.crm.contacts.basic_api.update(
            contact_id=contact_id,
            simple_public_object_input=SimplePublicObjectInput(
                properties={"hs_pinned_engagement_id": str(note_id)}
            ),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        manual_pin_path = os.path.join(RUNNER_TEMP, "manual_pin_list.json")
        try:
            with open(manual_pin_path, encoding="utf-8") as f:
                pin_list = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pin_list = []
        pin_list.append({"contact_id": str(contact_id), "note_id": str(note_id)})
        with open(manual_pin_path, "w", encoding="utf-8") as f:
            json.dump(pin_list, f, indent=2)
        print(
            f"WARNING: pin failed for contact {contact_id} / note {note_id}: {exc}. "
            f"Added to manual_pin_list.json.",
            file=sys.stderr,
        )
        return False


def main() -> None:
    contact_id = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("INPUT_CONTACT_ID")
    if not contact_id:
        print("ERROR: contact_id not provided (argv[1] or INPUT_CONTACT_ID).", file=sys.stderr)
        sys.exit(1)

    assembled_path = os.path.join(RUNNER_TEMP, f"assembled_{contact_id}.json")
    write_dlq(contact_id, "", "startup", "sentinel", 0)

    client = hubspot.Client.create(access_token=HUBSPOT_API_KEY)
    _check_property_schema(client)

    assembled = None
    try:
        with open(assembled_path, encoding="utf-8") as f:
            assembled = json.load(f)

        pin_body = assembled.get("pin", {}).get("body")
        if not pin_body:
            raise ValueError(f"Missing 'pin.body' in assembled JSON for contact {contact_id}")

        _bracket_guard(assembled)
        properties = _build_properties(assembled)
        _write_properties(client, contact_id, properties)
        print(f"Properties written for contact {contact_id}")

    except Exception as exc:
        contact_email = assembled.get("contact_id", str(contact_id)) if assembled else str(contact_id)
        write_dlq(contact_id, contact_email, "validate_or_write", str(exc), 0)
        print(f"ERROR {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        pin_body = assembled["pin"]["body"]
        note_id = _create_note_object(client, pin_body, str(contact_id))
        _pin_note(str(contact_id), note_id, client)
        print(f"Note created and pin attempted for contact {contact_id}")
    except Exception as exc:
        write_dlq(contact_id, assembled.get("contact_id", str(contact_id)), "create_or_pin_note", str(exc), 0)
        print(f"ERROR note for {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Write-back complete for contact {contact_id}.")


if __name__ == "__main__":
    main()
