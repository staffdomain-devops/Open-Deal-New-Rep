"""agent2_build.py — Agent 2 (Build) for the Lane A re-engagement pipeline.

Usage:
    python scripts/agent2_build.py <contact_id>

Reads $RUNNER_TEMP/research_{id}.json (written by agent1_research.py), calls
Claude with the locked voice/mechanics system prompt to generate the 8
deliverables (5 emails + 2 call-task notes + 1 pin note), and writes
$RUNNER_TEMP/generated_{id}.json.

Eligibility is decided upstream (the HubSpot workflow that fires the webhook,
then Agent 1's brief assembly); every contact_id this script receives is
expected to already be an eligible candidate. This script does no hold/
exclude filtering of its own — it only analyzes research["brief_text"] and
generates the deliverables from it.
"""

import json
import os
import sys

import anthropic
from tenacity import retry

from config.system_prompt import get_system_prompt
from utils import ANTHROPIC_RETRY_KWARGS, write_dlq

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
SYSTEM_MESSAGE = [
    {
        "type": "text",
        "text": get_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]
EMAIL_KEYS = ["e1", "e2", "e3", "e4", "e5"]
CALL_KEYS = ["call1", "call2", "pin"]
ALL_KEYS = EMAIL_KEYS + CALL_KEYS


class MaxTokensError(Exception):
    """Raised when model stops at max_tokens limit."""


class OutputParseError(Exception):
    """Raised when 8-key JSON cannot be parsed from model response."""


def _extract_text(response: anthropic.types.Message, contact_id: str) -> str:
    """Return the first text block's content, skipping ThinkingBlocks and any
    other non-text content blocks (extended thinking prepends a ThinkingBlock
    when enabled, so content[0] is not reliably the text block)."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise OutputParseError(f"No text block found in response content for {contact_id}")


def parse_output(response: anthropic.types.Message, contact_id: str) -> dict:
    if response.stop_reason == "max_tokens":
        raise MaxTokensError(f"max_tokens reached for contact {contact_id}")

    raw_text = _extract_text(response, contact_id)

    text = raw_text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raw_path = os.path.join(RUNNER_TEMP, f"raw_response_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(f"JSON decode failed for {contact_id}: {exc}") from exc

    missing = [k for k in ALL_KEYS if k not in parsed]
    if missing:
        raw_path = os.path.join(RUNNER_TEMP, f"raw_response_{contact_id}.txt")
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(raw_text)
        raise OutputParseError(f"Missing top-level keys for {contact_id}: {missing}")

    for key in EMAIL_KEYS:
        for sub in ("subject", "body"):
            if sub not in parsed[key]:
                raise OutputParseError(f"Missing sub-key '{sub}' in {key} for {contact_id}")

    for key in CALL_KEYS:
        if "body" not in parsed[key]:
            raise OutputParseError(f"Missing sub-key 'body' in {key} for {contact_id}")

    return parsed


def write_generated(contact_id: str, parsed: dict, research: dict) -> None:
    payload = {
        "contact_id": contact_id,
        "e1": parsed["e1"],
        "e2": parsed["e2"],
        "e3": parsed["e3"],
        "e4": parsed["e4"],
        "e5": parsed["e5"],
        "call1": parsed["call1"],
        "call2": parsed["call2"],
        "pin": parsed["pin"],
        "case_study": research.get("case_study"),
        "close_option": research.get("close_option"),
        "close_text": research.get("close_text"),
        "contact_first_name": research.get("contact_first_name", ""),
    }
    out_path = os.path.join(RUNNER_TEMP, f"generated_{contact_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


# Sonnet 5 runs adaptive thinking by default even when `thinking` isn't set,
# and thinking tokens share the same max_tokens budget as the visible JSON
# output, so the ceiling needs headroom well beyond the ~550-word deliverable
# text. Escalate on a max_tokens hit instead of failing outright: some briefs
# push the model to reason more than others, and refusing to grow the budget
# just moves the failure to the next contact that reasons a bit more.
MAX_TOKENS_BUDGETS = [16000, 32000]


@retry(**ANTHROPIC_RETRY_KWARGS)
def _call_realtime(brief_text: str, max_tokens: int) -> anthropic.types.Message:
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=max_tokens,
        system=SYSTEM_MESSAGE,
        messages=[{"role": "user", "content": brief_text}],
    )


def main():
    contact_id = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("INPUT_CONTACT_ID")
    if not contact_id:
        print("ERROR: contact_id not provided (argv[1] or INPUT_CONTACT_ID).", file=sys.stderr)
        sys.exit(1)

    research_path = os.path.join(RUNNER_TEMP, f"research_{contact_id}.json")
    with open(research_path, encoding="utf-8") as f:
        research = json.load(f)

    write_dlq(contact_id, "", "startup", "sentinel", 0)

    try:
        response = None
        parsed = None
        last_exc = None
        for i, budget in enumerate(MAX_TOKENS_BUDGETS):
            response = _call_realtime(research["brief_text"], budget)
            try:
                parsed = parse_output(response, contact_id)
                last_exc = None
                break
            except MaxTokensError as exc:
                last_exc = exc
                remaining = len(MAX_TOKENS_BUDGETS) - i - 1
                if remaining:
                    print(
                        f"WARNING {contact_id}: max_tokens={budget} reached, "
                        f"retrying with a larger budget ({remaining} attempt(s) left)",
                        file=sys.stderr,
                    )
        if last_exc is not None:
            raise last_exc

        write_generated(contact_id, parsed, research)
        usage = response.usage
        print(
            f"Generated {contact_id}. Cache usage: "
            f"cache_creation={usage.cache_creation_input_tokens} "
            f"cache_read={usage.cache_read_input_tokens}"
        )
    except (MaxTokensError, OutputParseError) as exc:
        write_dlq(contact_id, "", "parse_output", str(exc), 0)
        print(f"ERROR parse_output {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        write_dlq(contact_id, "", "agent2_build", str(exc), 0)
        print(f"ERROR agent2_build {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
