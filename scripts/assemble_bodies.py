"""assemble_bodies.py — Append the code-supplied parts of each deliverable.

Two jobs, both deliberately AFTER lint:
  - Link placeholders onto the E2 and E5 email bodies (spec §6).
  - The fixed boilerplate lines onto the call notes and pin note, which the
    model does not generate (see config/call_note_boilerplate.py for why).

Usage:
    python scripts/assemble_bodies.py <contact_id>
"""

import json
import os
import re
import sys

from config.call_note_boilerplate import CALL2_WHY, PIN_APPENDIX, call1_why
from utils import write_dlq

RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")

_WHY_THIS_CALL_RE = re.compile(r"^[ \t]*WHY THIS CALL\b[^\n:]*:", re.IGNORECASE | re.MULTILINE)
# Defensive only: the pin prompt no longer uses a "RULES:" label at all, but
# if a drifting model ever emits one anyway, don't double up the appendix.
_RULES_RE = re.compile(r"^[ \t]*RULES\b[^\n:]*:", re.IGNORECASE | re.MULTILINE)


def _append_placeholder(body: str, placeholder: str) -> str:
    return body + "\n" + placeholder


def _prepend_why(body: str, why_line: str) -> str:
    """Put the fixed WHY THIS CALL line at the top of a call note.

    No-op if the model emitted one anyway, so a prompt that drifts produces a
    note that is merely non-canonical rather than one with two opening lines.
    """
    if _WHY_THIS_CALL_RE.search(body):
        return body
    return why_line + "\n" + body.lstrip("\n")


def _append_pin_appendix(body: str) -> str:
    """Put the fixed plan+rules appendix at the bottom of the pin note."""
    if _RULES_RE.search(body):
        return body
    return body.rstrip("\n") + PIN_APPENDIX


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
        for key in ("e2", "e5", "call1", "call2", "pin"):
            assembled[key] = dict(generated[key])

        # Only append the manual-fill placeholder when routing had no
        # verified case-study URL. When it did, the model already wove the
        # real URL into the body inline (lint H06 enforces this), so
        # appending a placeholder on top would be a second, unwanted link.
        if not generated.get("case_study_url"):
            case_study = generated.get("case_study", "case study")
            assembled["e2"]["body"] = _append_placeholder(
                generated["e2"]["body"],
                f"[Insert {case_study} case study link here]",
            )
        assembled["e5"]["body"] = _append_placeholder(
            generated["e5"]["body"],
            "[Insert rep booking link here]",
        )

        assembled["call1"]["body"] = _prepend_why(
            generated["call1"]["body"],
            call1_why(generated.get("contact_first_name", "")),
        )
        assembled["call2"]["body"] = _prepend_why(generated["call2"]["body"], CALL2_WHY)
        assembled["pin"]["body"] = _append_pin_appendix(generated["pin"]["body"])

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
