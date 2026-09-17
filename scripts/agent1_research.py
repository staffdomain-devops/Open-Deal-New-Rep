"""agent1_research.py — Agent 1 (Research) for the Lane A re-engagement pipeline.

Usage:
    python scripts/agent1_research.py <contact_id>

Reads $RUNNER_TEMP/context_{id}.json (written by fetch_context.py), computes
vertical routing and close-bank assignment deterministically, calls Claude to
analyze the record and produce a research brief, and writes
$RUNNER_TEMP/research_{id}.json.

Eligibility is decided upstream by the HubSpot workflow that fires the
webhook; every contact this script receives is expected to be eligible.
"""

import json
import os
import re
import sys

import anthropic
from tenacity import retry

from config.close_bank import assign_close
from config.research_system_prompt import get_research_system_prompt
from config.vertical_routing import route, ROUTING_TABLE
from utils import ANTHROPIC_RETRY_KWARGS, write_dlq

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
SYSTEM_MESSAGE = [
    {
        "type": "text",
        "text": get_research_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]

REQUIRED_KEYS = [
    "brief_text", "sensitive_items", "handover_first_name", "colleague_first_names",
]


class OutputParseError(Exception):
    """Raised when Agent 1's JSON response cannot be parsed or is missing keys."""


# ---------------------------------------------------------------------------
# Deterministic pre-computation (routing, close bank, allowed-names)
# ---------------------------------------------------------------------------


def _industry_match_strength(industry: str) -> str:
    normalised = (industry or "").lower().strip()
    for row in ROUTING_TABLE:
        if not row["tokens"]:
            continue
        if any(token in normalised for token in row["tokens"]):
            return "exact"
    return "partial"


def _words(text: str) -> list:
    """Lower-cased word tokens, punctuation dropped rather than glued together.

    Splitting on whitespace alone would turn the routing table's
    "Carrera by Design (construction/joinery)" into one token per bracketed
    run; the values here are compared against whole words in lint, so they
    have to be whole words too.
    """
    return [w.lower() for w in re.findall(r"[A-Za-z0-9']+", text or "")]


def _company_name_words(context: dict) -> list:
    """The company's own name, word by word.

    Lint exempts these from the caricature list (H03) and the offshore
    vocabulary list (H04): a prospect called "Australian Outsourcing Broker"
    or "Sorted Digital Marketing" is entitled to be called that in the copy.
    Those rules are about vocabulary we choose, not about their letterhead.
    """
    return sorted(set(_words(context["company_props"].get("name"))))


def _allowed_names(context: dict, vr) -> list:
    """Broad name allow-list for lint's name-invention check (H10).

    Job titles and the case-study name are in here because the system prompt
    tells the model to use both — colleagues are introduced by title, and
    call1's EMAILS SO FAR names the case study. Without them H10 fires on
    ordinary correct copy: "Director" alone sits in 267 job titles on the
    current segment.
    """
    props = context["contact_props"]
    handover = context.get("handover") or {}
    colleagues = context.get("all_company_contacts", [])

    allowed = set(_company_name_words(context))
    for field in ("firstname", "lastname", "jobtitle"):
        allowed.update(_words(props.get(field)))
    allowed.update(_words(handover.get("first_name")))
    for c in colleagues:
        for field in ("firstname", "jobtitle"):
            allowed.update(_words(c.get(field)))
    allowed.update(_words(vr.case_study))
    return sorted(allowed)


def _build_user_message(context: dict, vr, close_option: int, close_text: str) -> str:
    payload = {
        "contact_props": context["contact_props"],
        "company_props": context["company_props"],
        "all_company_contacts": context["all_company_contacts"],
        "deals": context["deals"],
        "story_notes": context["story_notes"],
        "live_hiring_signals": context["live_hiring_signals"],
        "handover": context["handover"],
        "geo": context["geo"],
        "is_only_contact": context["is_only_contact"],
        "routing": {
            "case_study": vr.case_study,
            "email3_url": vr.email3_url,
            "email4_url": vr.email4_url,
        },
        "close": {"option": close_option, "text": close_text},
    }
    return json.dumps(payload, indent=2, default=str)


# ---------------------------------------------------------------------------
# Claude call
# ---------------------------------------------------------------------------


@retry(**ANTHROPIC_RETRY_KWARGS)
def _call_research(user_message: str) -> anthropic.types.Message:
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=8192,
        system=SYSTEM_MESSAGE,
        messages=[{"role": "user", "content": user_message}],
    )


def _extract_text(response: anthropic.types.Message, contact_id: str) -> str:
    """Return the first text block's content, skipping ThinkingBlocks and any
    other non-text content blocks (extended thinking prepends a ThinkingBlock
    when enabled, so content[0] is not reliably the text block)."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise OutputParseError(f"No text block found in response content for {contact_id}")


def _parse_output(response: anthropic.types.Message, contact_id: str) -> dict:
    raw_text = _extract_text(response, contact_id).strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    if response.stop_reason == "max_tokens":
        raw_path = os.path.join(RUNNER_TEMP, f"raw_research_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(
            f"max_tokens reached for contact {contact_id}; truncated output saved to {raw_path}"
        )

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raw_path = os.path.join(RUNNER_TEMP, f"raw_research_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(f"JSON decode failed for {contact_id}: {exc}") from exc

    missing = [k for k in REQUIRED_KEYS if k not in parsed]
    if missing:
        raw_path = os.path.join(RUNNER_TEMP, f"raw_research_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(f"Missing keys for {contact_id}: {missing}")

    return parsed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    contact_id = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("INPUT_CONTACT_ID")
    if not contact_id:
        print("ERROR: contact_id not provided (argv[1] or INPUT_CONTACT_ID).", file=sys.stderr)
        sys.exit(1)

    context_path = os.path.join(RUNNER_TEMP, f"context_{contact_id}.json")
    with open(context_path, encoding="utf-8") as f:
        context = json.load(f)

    write_dlq(contact_id, context["contact_props"].get("email", ""), "startup", "sentinel", 0)

    try:
        vr = route(context["contact_props"].get("industry") or "")
        strength = _industry_match_strength(context["contact_props"].get("industry") or "")
        touches = int(context["contact_props"].get("num_contacted_notes") or 0)
        jobtitle = context["contact_props"].get("jobtitle") or ""
        close_option, close_text = assign_close(touches, jobtitle, strength)

        user_message = _build_user_message(context, vr, close_option, close_text)
        response = _call_research(user_message)
        parsed = _parse_output(response, contact_id)

        research = {
            "contact_id": str(contact_id),
            "brief_text": parsed["brief_text"],
            "sensitive_items": parsed["sensitive_items"],
            "handover_first_name": parsed["handover_first_name"],
            "colleague_first_names": parsed["colleague_first_names"],
            "geo": context["geo"],
            # Carried through for assemble_bodies.py, which fills it into
            # call1's code-supplied WHY THIS CALL line.
            "contact_first_name": context["contact_props"].get("firstname") or "",
            "allowed_names": _allowed_names(context, vr),
            "company_name_words": _company_name_words(context),
            "case_study": vr.case_study,
            "email3_url": vr.email3_url,
            "email4_url": vr.email4_url,
            "close_option": close_option,
            "close_text": close_text,
        }

        out_path = os.path.join(RUNNER_TEMP, f"research_{contact_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(research, f, indent=2)
        print(f"Written {out_path}")

    except Exception as exc:
        write_dlq(contact_id, context["contact_props"].get("email", ""), "agent1_research", str(exc), 0)
        print(f"ERROR agent1_research {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
