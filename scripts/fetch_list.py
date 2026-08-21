"""fetch_list.py — Fetch all contact IDs from a HubSpot list.

Reads INPUT_LIST_ID from the environment, fetches all pages of memberships from
the HubSpot Lists API v3, and writes a JSON array of contact ID strings to
$RUNNER_TEMP/contact_ids.json.

Usage (GitHub Actions step):
    python scripts/fetch_list.py
"""

import json
import os
import sys

import requests
from tenacity import retry

from utils import write_dlq, REQ_RETRY_KWARGS

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

try:
    INPUT_LIST_ID = os.environ["INPUT_LIST_ID"]
except KeyError:
    print("ERROR: INPUT_LIST_ID environment variable is not set.", file=sys.stderr)
    sys.exit(1)

HUBSPOT_API_KEY = os.environ["HUBSPOT_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")

# ---------------------------------------------------------------------------
# DLQ sentinel — written at startup so a crash before the try/except still
# leaves a trace (satisfies ERR-02 / T-02-03).
# ---------------------------------------------------------------------------

write_dlq(INPUT_LIST_ID, "", "startup", "sentinel", 0)

# ---------------------------------------------------------------------------
# HubSpot Lists API v3 — cursor-paginated page fetch
# ---------------------------------------------------------------------------

_LIST_URL = "https://api.hubapi.com/crm/v3/lists/{list_id}/memberships"
_HEADERS = {"Authorization": f"Bearer {HUBSPOT_API_KEY}"}


@retry(**REQ_RETRY_KWARGS)
def fetch_page(list_id: str, after: str | None = None) -> dict:
    """Fetch one page of list memberships.

    Applies REQ_RETRY_KWARGS (exponential backoff + HubSpot ms Retry-After)
    so transient 429 / 5xx responses are retried automatically.
    """
    params = {}
    if after is not None:
        params["after"] = after

    response = requests.get(
        _LIST_URL.format(list_id=list_id),
        headers=_HEADERS,
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# Main fetch + write
# ---------------------------------------------------------------------------

try:
    contact_ids: list[str] = []
    after: str | None = None

    while True:
        data = fetch_page(INPUT_LIST_ID, after)

        for record in data.get("results", []):
            contact_ids.append(str(record["recordId"]))

        # Pagination: stop when "paging" key or "next" sub-key is absent.
        after = (
            data.get("paging", {})
            .get("next", {})
            .get("after")
        )
        if after is None:
            break

    # Write the complete list in a single dump — never a partial file.
    output_path = os.path.join(RUNNER_TEMP, "contact_ids.json")
    with open(output_path, "w") as f:
        json.dump(contact_ids, f)

    print(f"Fetched {len(contact_ids)} contact IDs from list {INPUT_LIST_ID}")

except Exception as e:
    write_dlq(INPUT_LIST_ID, "", "fetch_list", str(e), 0)
    sys.exit(1)
