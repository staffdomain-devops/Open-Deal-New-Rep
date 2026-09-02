"""agent1_research.py — Agent 1 (Research) for the Lane A re-engagement pipeline.

Usage:
    python scripts/agent1_research.py <contact_id>

Reads $RUNNER_TEMP/context_{id}.json (written by fetch_context.py), computes
vertical routing and close-bank assignment deterministically, calls Claude to
analyze the record and produce a verdict + research brief, and writes
$RUNNER_TEMP/research_{id}.json.

If the verdict is not PROCEED, the pipeline stops here: a Teams notification
is sent (if configured) and the script exits 0 — a hold/exclude is an
intentional outcome, not a failure.
"""

import json
import os
import sys

import anthropic
import requests
from tenacity import retry

from config.close_bank import assign_close
from config.research_system_prompt import get_research_system_prompt
from config.vertical_routing import route, ROUTING_TABLE
from utils import ANTHROPIC_RETRY_KWARGS, write_dlq

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
SYSTEM_MESSAGE = [
    {
        "type": "text",
        "text": get_research_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]

REQUIRED_KEYS = [
    "verdict", "filter_code", "reasoning", "brief_text",
    "sensitive_items", "handover_first_name", "colleague_first_names",
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


def _allowed_names(context: dict) -> list:
    """Broad name allow-list for lint's name-invention check (H10)."""
    props = context["contact_props"]
    company_props = context["company_props"]
    handover = context.get("handover") or {}
    colleagues = context.get("all_company_contacts", [])

    allowed = set()
    for field in ("firstname", "lastname"):
        v = props.get(field)
        if v:
            allowed.add(v.lower())
    if handover.get("first_name"):
        allowed.add(handover["first_name"].lower())
    for c in colleagues:
        v = c.get("firstname")
        if v:
            allowed.add(v.lower())
    company_name = company_props.get("name") or ""
    for word in company_name.split():
        allowed.add(word.lower())
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
        "departure_flagged": context["departure_flagged"],
        "possible_duplicates": context["possible_duplicates"],
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
# Notification
# ---------------------------------------------------------------------------


def _notify_hold_exclude(contact_id: str, verdict: str, filter_code: str, reasoning: str) -> None:
    if not TEAMS_WEBHOOK_URL:
        print(f"{verdict} {contact_id} ({filter_code}): {reasoning}")
        return
    try:
        requests.post(
            TEAMS_WEBHOOK_URL,
            json={
                "text": f"Lane A {verdict}: contact {contact_id} ({filter_code}). {reasoning}",
                "contact_id": contact_id,
                "verdict": verdict,
                "filter_code": filter_code,
            },
            timeout=15,
        )
    except Exception as exc:
        print(f"WARNING: Teams notification failed for {contact_id}: {exc}", file=sys.stderr)


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
            "verdict": parsed["verdict"],
            "filter_code": parsed["filter_code"],
            "reasoning": parsed["reasoning"],
            "brief_text": parsed["brief_text"],
            "sensitive_items": parsed["sensitive_items"],
            "handover_first_name": parsed["handover_first_name"],
            "colleague_first_names": parsed["colleague_first_names"],
            "geo": context["geo"],
            "allowed_names": _allowed_names(context),
            "case_study": vr.case_study,
            "email3_url": vr.email3_url,
            "email4_url": vr.email4_url,
            "close_option": close_option,
            "close_text": close_text,
        }

        out_path = os.path.join(RUNNER_TEMP, f"research_{contact_id}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(research, f, indent=2)
        print(f"Written {out_path} (verdict={research['verdict']})")

        if research["verdict"] != "PROCEED":
            _notify_hold_exclude(
                contact_id, research["verdict"], research["filter_code"], research["reasoning"]
            )

        github_output = os.environ.get("GITHUB_OUTPUT")
        if github_output:
            with open(github_output, "a", encoding="utf-8") as f:
                f.write(f"verdict={research['verdict']}\n")

    except Exception as exc:
        write_dlq(contact_id, context["contact_props"].get("email", ""), "agent1_research", str(exc), 0)
        print(f"ERROR agent1_research {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
