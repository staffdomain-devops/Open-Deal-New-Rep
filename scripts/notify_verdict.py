"""notify_verdict.py — HubSpot note write-back for Agent 1 HOLD/EXCLUDE verdicts.

Usage:
    python scripts/notify_verdict.py <contact_id>

Reads $RUNNER_TEMP/research_{id}.json (written by agent1_research.py) and, for
a non-PROCEED verdict, writes a HubSpot note on the contact stating the
verdict, filter code, and Agent 1's reasoning only. The research brief,
sensitive items, and other research materials are deliberately left out —
this note explains why the contact didn't proceed, nothing more.
"""

import json
import os
import sys
from datetime import datetime, timezone

import hubspot
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


def _build_note_body(research: dict) -> str:
    return (
        "Lane A Re-Engagement Pipeline — did not proceed\n\n"
        f"Verdict: {research['verdict']} ({research['filter_code']})\n\n"
        f"Reason: {research['reasoning']}"
    )


@retry(**HS_RETRY_KWARGS)
def _create_note(client, body: str, contact_id: str) -> str:
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


def main() -> None:
    contact_id = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("INPUT_CONTACT_ID")
    if not contact_id:
        print("ERROR: contact_id not provided (argv[1] or INPUT_CONTACT_ID).", file=sys.stderr)
        sys.exit(1)

    research_path = os.path.join(RUNNER_TEMP, f"research_{contact_id}.json")
    write_dlq(contact_id, "", "startup", "sentinel", 0)

    try:
        with open(research_path, encoding="utf-8") as f:
            research = json.load(f)

        if research["verdict"] == "PROCEED":
            print(f"Verdict is PROCEED for {contact_id}; no verdict note needed.")
            return

        client = hubspot.Client.create(access_token=HUBSPOT_API_KEY)
        body = _build_note_body(research)
        note_id = _create_note(client, body, str(contact_id))
        print(
            f"Verdict note ({research['verdict']}/{research['filter_code']}) "
            f"written for contact {contact_id}"
        )

    except Exception as exc:
        write_dlq(contact_id, "", "notify_verdict", str(exc), 0)
        print(f"ERROR notify_verdict {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
