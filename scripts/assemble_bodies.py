"""assemble_bodies.py — Append link placeholders to E2 and E5 email bodies.

Usage:
    python scripts/assemble_bodies.py <contact_id>
"""

import json
import os
import sys

from utils import write_dlq

RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")


def _append_placeholder(body: str, placeholder: str) -> str:
    return body + "\n" + placeholder


def main():
    contact_id = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("INPUT_CONTACT_ID")
    if not contact_id:
        print("ERROR: contact_id not provided (argv[1] or INPUT_CONTACT_ID).", file=sys.stderr)
        sys.exit(1)

    gen_path = os.path.join(RUNNER_TEMP, f"generated_{contact_id}.json")
    write_dlq(contact_id, "", "startup", "sentinel", 0)

    try:
        with open(gen_path, encoding="utf-8") as f:
            generated = json.load(f)

        assembled = {k: v for k, v in generated.items()}
        assembled["e2"] = dict(generated["e2"])
        assembled["e5"] = dict(generated["e5"])

        case_study = generated.get("case_study", "case study")
        assembled["e2"]["body"] = _append_placeholder(
            generated["e2"]["body"],
            f"[Insert {case_study} case study link here]",
        )
        assembled["e5"]["body"] = _append_placeholder(
            generated["e5"]["body"],
            "[Insert rep booking link here]",
        )

        out_path = os.path.join(RUNNER_TEMP, f"assembled_{contact_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(assembled, f, indent=2, default=str)

        print(f"Assembled {contact_id}")

    except Exception as exc:
        write_dlq(contact_id, "", "assemble_bodies", str(exc), 0)
        print(f"ERROR assemble {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
