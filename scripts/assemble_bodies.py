"""assemble_bodies.py — Append link placeholders to E2 and E5 email bodies.

Usage:
    python scripts/assemble_bodies.py
"""

import json
import os
import sys

from utils import write_dlq

RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")


def _append_placeholder(body: str, placeholder: str) -> str:
    return body + "\n" + placeholder


def main():
    ids_path = os.path.join(RUNNER_TEMP, "lint_passing_ids.json")
    with open(ids_path) as f:
        contact_ids = json.load(f)

    write_dlq("batch", "", "startup", "sentinel", 0)

    assembled_count = 0
    total = len(contact_ids)

    for cid in contact_ids:
        gen_path = os.path.join(RUNNER_TEMP, f"generated_{cid}.json")
        if not os.path.exists(gen_path):
            print(f"SKIP {cid}: generated file not found", file=sys.stderr)
            continue

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

            out_path = os.path.join(RUNNER_TEMP, f"assembled_{cid}.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(assembled, f, indent=2, default=str)

            assembled_count += 1

        except Exception as exc:
            write_dlq(cid, "", "assemble_bodies", str(exc), 0)
            print(f"ERROR assemble {cid}: {exc}", file=sys.stderr)

    print(f"Assembly complete: {assembled_count}/{total} contacts assembled.")


if __name__ == "__main__":
    main()
