"""assemble_output.py — Aggregate assembled per-contact JSON files into campaign_output.json."""

import json
import os
import sys

from utils import write_dlq

RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")


def main():
    ids_path = os.path.join(RUNNER_TEMP, "lint_passing_ids.json")
    try:
        with open(ids_path) as f:
            contact_ids = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: lint_passing_ids.json not found at {ids_path}", file=sys.stderr)
        sys.exit(1)

    total = len(contact_ids)
    results = []
    collected_count = 0

    write_dlq("batch", "", "startup", "sentinel", 0)

    for cid in contact_ids:
        assembled_path = os.path.join(RUNNER_TEMP, f"assembled_{cid}.json")
        try:
            with open(assembled_path, encoding="utf-8") as f:
                data = json.load(f)
            results.append(data)
            collected_count += 1
        except FileNotFoundError:
            print(f"SKIP {cid}: assembled file not found", file=sys.stderr)
            continue
        except Exception as exc:
            write_dlq(cid, "", "assemble_output", str(exc), 0)
            print(f"ERROR {cid}: {exc}", file=sys.stderr)
            continue

    out_path = os.path.join(RUNNER_TEMP, "campaign_output.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)

    print(f"Output aggregation complete: {collected_count}/{total} contacts collected.")


if __name__ == "__main__":
    main()
