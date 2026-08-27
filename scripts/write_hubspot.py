"""write_hubspot.py — Write assembled contact properties to HubSpot.

Reads lint_passing_ids.json and assembled_{cid}.json from RUNNER_TEMP,
validates property schema, guards against unresolved bracket placeholders,
then batch-writes all 12 contact properties to HubSpot.

Usage:
    python scripts/write_hubspot.py
"""

import json
import os
import sys
from datetime import datetime, timezone

import hubspot
from hubspot.crm.contacts import (
    BatchInputSimplePublicObjectBatchInput,
    SimplePublicObjectBatchInput,
    SimplePublicObjectInput,
)
from hubspot.crm.objects.notes import SimplePublicObjectInputForCreate
from tenacity import retry

from utils import write_dlq, HS_RETRY_KWARGS

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

HUBSPOT_API_KEY = os.environ["HUBSPOT_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")
BATCH_SIZE = 100

# Ordered list of (hubspot_property_name, (top_key, field)) for all 12 properties.
PROPERTY_MAP = [
    ("email_1_subject", ("e1", "subject")),
    ("email_1_body",    ("e1", "body")),
    ("email_2_subject", ("e2", "subject")),
    ("email_2_body",    ("e2", "body")),
    ("email_3_subject", ("e3", "subject")),
    ("email_3_body",    ("e3", "body")),
    ("email_4_subject", ("e4", "subject")),
    ("email_4_body",    ("e4", "body")),
    ("email_5_subject", ("e5", "subject")),
    ("email_5_body",    ("e5", "body")),
    ("task_note_1",     ("call1", "body")),
    ("task_note_2",     ("call2", "body")),
]

# Frozenset of the 10 email property names only; task_note_1 and task_note_2
# are intentionally excluded so the bracket guard never scans call briefings.
EMAIL_PROP_NAMES = frozenset(
    prop_name
    for prop_name, _ in PROPERTY_MAP
    if prop_name.startswith("email_")
)


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def _check_property_schema(client) -> None:
    """Verify all 12 HubSpot contact properties have field_type == 'textarea'.

    Exits with code 1 on the first mismatch — a wrong field_type is a
    configuration error, not a transient failure, so this function is NOT
    decorated with @retry.
    """
    for prop_name, _ in PROPERTY_MAP:
        resp = client.crm.properties.core_api.get_by_name("contacts", prop_name)
        actual = resp.field_type
        if actual != "textarea":
            print(
                f"ERROR: HubSpot property '{prop_name}' has field_type='{actual}', "
                f"expected 'textarea'. Fix in HubSpot before running write-back."
            )
            sys.exit(1)
    print("Property schema check passed: all 12 properties are textarea.")


def _bracket_guard(assembled: dict) -> None:
    """Raise ValueError if any email property value contains an unresolved '[' placeholder.

    Only scans the 10 EMAIL_PROP_NAMES properties. task_note_1 and task_note_2
    are never scanned.
    """
    for prop_name, (top_key, field) in PROPERTY_MAP:
        if prop_name not in EMAIL_PROP_NAMES:
            continue
        section = assembled.get(top_key)
        if section is None:
            raise ValueError(
                f"Bracket guard: missing top-level key '{top_key}' in assembled JSON"
            )
        value = section.get(field)
        if value is None:
            raise ValueError(
                f"Bracket guard: missing field '{field}' under '{top_key}'"
            )
        if "[" in value:
            raise ValueError(
                f"Bracket guard: unresolved placeholder in '{prop_name}' for contact "
                f"{assembled.get('contact_id')}. Value starts: {value[:80]!r}"
            )


def _build_batch_input(contact_id: str, assembled: dict) -> dict:
    """Return a plain dict suitable for wrapping in SimplePublicObjectBatchInput."""
    properties = {
        prop_name: assembled[top_key][field]
        for prop_name, (top_key, field) in PROPERTY_MAP
    }
    return {"id": contact_id, "properties": properties}


@retry(**HS_RETRY_KWARGS)
def _write_properties_batch(client, batch_inputs: list) -> None:
    """Write a batch of contact property updates to HubSpot.

    Decorated with @retry(**HS_RETRY_KWARGS) — transient 429/5xx errors are
    retried with exponential backoff + HubSpot Retry-After header.
    """
    inputs = [
        SimplePublicObjectBatchInput(id=d["id"], properties=d["properties"])
        for d in batch_inputs
    ]
    client.crm.contacts.batch_api.update(
        batch_input_simple_public_object_batch_input=BatchInputSimplePublicObjectBatchInput(
            inputs=inputs
        )
    )


@retry(**HS_RETRY_KWARGS)
def _create_note(client, contact_id: str, body: str) -> str:
    """Create a HubSpot note with the given body and associate it with the contact.

    Decorated with @retry(**HS_RETRY_KWARGS) — transient 429/5xx errors are
    retried with exponential backoff + HubSpot Retry-After header.

    Returns the note_id string.
    """
    note_input = SimplePublicObjectInputForCreate(
        properties={
            "hs_note_body": body,
            "hs_timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    response = client.crm.objects.notes.basic_api.create(
        simple_public_object_input_for_create=note_input
    )
    note_id = response.id

    client.crm.objects.notes.associations_api.create(
        note_id=note_id,
        to_object_type="contacts",
        to_object_id=contact_id,
        association_type="note_to_contact",
    )

    return str(note_id)


def _pin_note(contact_id: str, note_id: str, client) -> bool:
    """Attempt to pin a note on the contact via hs_pinned_engagement_id.

    NOT decorated with @retry — pin failure is a soft fallback, not a retriable
    error. On success returns True. On any Exception, appends
    {"contact_id": str, "note_id": str} to manual_pin_list.json in RUNNER_TEMP
    using read-modify-write, prints a warning to stderr, and returns False.
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
    # Step 1: Load lint_passing_ids.json
    ids_path = os.path.join(RUNNER_TEMP, "lint_passing_ids.json")
    with open(ids_path) as f:
        contact_ids = json.load(f)

    # Step 2: DLQ sentinel — written before any contact work (ERR-02)
    write_dlq("batch", "", "startup", "sentinel", 0)

    # Step 3: Instantiate HubSpot client
    client = hubspot.Client.create(access_token=HUBSPOT_API_KEY)

    # Step 4: Verify property schema before entering contact loop
    _check_property_schema(client)

    # Step 5: Initialise batch state
    pending_batch = []
    written_count = 0
    error_count = 0

    # Step 6: Loop over contacts
    for cid in contact_ids:
        assembled_path = os.path.join(RUNNER_TEMP, f"assembled_{cid}.json")
        if not os.path.exists(assembled_path):
            print(f"SKIP {cid}: assembled file not found", file=sys.stderr)
            continue

        try:
            with open(assembled_path, encoding="utf-8") as f:
                assembled = json.load(f)

            _bracket_guard(assembled)
            input_dict = _build_batch_input(str(cid), assembled)
            pending_batch.append(input_dict)

            if len(pending_batch) == BATCH_SIZE:
                _write_properties_batch(client, pending_batch)
                written_count += len(pending_batch)
                pending_batch = []

            note_id = _create_note(client, str(cid), assembled["pin"]["body"])
            _pin_note(str(cid), note_id, client)

        except Exception as exc:
            write_dlq(cid, assembled.get("contact_id", str(cid)), "write_properties_or_note", str(exc), 0)
            error_count += 1
            print(f"ERROR {cid}: {exc}", file=sys.stderr)
            continue

    # Step 7: Flush remaining batch
    if pending_batch:
        _write_properties_batch(client, pending_batch)
        written_count += len(pending_batch)

    # Step 8: Summary
    print(f"Write-back complete: {written_count} contacts written, {error_count} errors.")


if __name__ == "__main__":
    main()
