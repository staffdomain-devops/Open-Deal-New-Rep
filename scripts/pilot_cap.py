"""pilot_cap.py — Slice contact_ids.json to 20 when pilot mode is active."""

import json
import os
import sys

RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")


def main():
    ids_path = os.path.join(RUNNER_TEMP, "contact_ids.json")
    with open(ids_path) as f:
        ids = json.load(f)

    original = len(ids)
    pilot = os.environ.get("PILOT_MODE", "true").lower()

    if pilot == "true" and original > 20:
        ids = ids[:20]
        print(f"PILOT MODE: capping {original} contacts to 20")
        with open(ids_path, "w") as f:
            json.dump(ids, f)

    print(f"Processing {len(ids)} contacts")


if __name__ == "__main__":
    main()
