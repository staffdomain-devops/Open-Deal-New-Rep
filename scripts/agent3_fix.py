"""agent3_fix.py — Agent 3 (Fix) for the Lane A re-engagement pipeline.

Usage (as a library, called from lint.py — not a standalone workflow step):
    from agent3_fix import fix_generated
    fixed = fix_generated(contact_id, research, generated, hard_failures)

Called by lint.py when attempt 1 hard-fails. Replaces the old blind
regeneration (re-running the whole brief through Agent 2 with no knowledge of
what went wrong), which in production made things worse at least once: a
contact whose only failure was call2 running long (108 words) came back from
a full regeneration with call1 now at 124 words — a deliverable that had
originally passed.

Agent 3 is told exactly which lint checks failed and, where derivable from
the check's own message, which deliverable(s) they concern. It is instructed
to rewrite ONLY those deliverables and echo everything else unchanged — and
the code does not merely trust that instruction: any key not implicated by a
failure is forced back to its pre-fix value after the call, so a passing
deliverable cannot regress no matter what the model does with it.

Uses the same system prompt (voice/mechanics rules, word budgets, label
skeletons) as Agent 2 — this is a correction pass against the same rules,
not a different voice or a different process. The system prompt is shared
across both agents' cache, so this call reads from the same cache entry
Agent 2 already primed.
"""

import json
import os
import re

import anthropic
from tenacity import retry

from agent2_build import OutputParseError
from config.system_prompt import get_system_prompt
from utils import ANTHROPIC_RETRY_KWARGS

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
SYSTEM_MESSAGE = [
    {
        "type": "text",
        "text": get_system_prompt(),
        "cache_control": {"type": "ephemeral"},
    }
]

ALL_KEYS = ["e1", "e2", "e3", "e4", "e5", "call1", "call2", "pin"]
EMAIL_KEYS = ["e1", "e2", "e3", "e4", "e5"]
CALL_KEYS = ["call1", "call2", "pin"]
KEY_RE = re.compile(r"\b(e[1-5]|call1|call2|pin)\b")

# Checks H02/H03/H04/H11/H12/H19 scan the FIVE EMAILS' text combined
# (_all_email_text in lint.py) and their failure messages don't name a
# single email, so a hit here means "one of the five emails" — all five are
# handed back for reconsideration rather than guessing which one.
WHOLE_EMAIL_SET_PREFIXES = ("H02:", "H03:", "H04:", "H11:", "H12:", "H19:")

# H16 ("no DO NOT SAY line found in call notes") checks call1, call2 AND pin
# together and its message doesn't name which one is missing the line, so
# treat it as "one of the three internal notes" rather than falling back to
# the full 8-key set.
WHOLE_CALL_SET_PREFIXES = ("H16:",)

FIX_MAX_TOKENS_BUDGETS = [16000, 32000]


def _keys_to_fix(hard_failures: list) -> set:
    """Map lint failure strings to the deliverable key(s) each one concerns.

    Falls back to "everything" only for a failure this function cannot
    attribute to a specific key or the whole-email set (in practice, H01
    schema failures) — safer than guessing wrong and leaving a real problem
    untouched.
    """
    keys = set()
    for reason in hard_failures:
        if reason.startswith(WHOLE_EMAIL_SET_PREFIXES):
            keys.update(EMAIL_KEYS)
            continue
        if reason.startswith(WHOLE_CALL_SET_PREFIXES):
            keys.update(CALL_KEYS)
            continue
        match = KEY_RE.search(reason)
        if match:
            keys.add(match.group(1))
        else:
            keys.update(ALL_KEYS)
    return keys


def _build_user_message(
    brief_text: str, generated: dict, hard_failures: list, keys_to_fix: set
) -> str:
    current_output = {k: generated[k] for k in ALL_KEYS}
    payload = {
        "brief_text": brief_text,
        "current_output": current_output,
        "lint_failures": hard_failures,
        "deliverables_to_fix": sorted(keys_to_fix),
    }
    preamble = (
        "FIX MODE. current_output is your own previous response to this exact "
        "brief. It failed the lint checks listed in lint_failures. "
        "deliverables_to_fix names exactly which top-level keys need "
        "correcting — rewrite ONLY those keys, applying every rule from your "
        "system prompt as normal, including the per-label word budgets and "
        "the mandatory final self-check for call1/call2/pin where relevant. "
        "Every key NOT listed in deliverables_to_fix must be returned "
        "character-for-character identical to its value in current_output: "
        "do not improve, rephrase, shorten, or vary anything that was not "
        "named as broken, even if you would have written it differently. "
        "Return the complete 8-key JSON in the same schema as always, no "
        "preamble, no markdown fences.\n\n"
    )
    return preamble + json.dumps(payload, indent=2, default=str)


@retry(**ANTHROPIC_RETRY_KWARGS)
def _call_fix(user_message: str, max_tokens: int) -> anthropic.types.Message:
    return client.messages.create(
        model="claude-sonnet-5",
        max_tokens=max_tokens,
        system=SYSTEM_MESSAGE,
        messages=[{"role": "user", "content": user_message}],
    )


def _extract_text(response: anthropic.types.Message, contact_id: str) -> str:
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    raise OutputParseError(f"No text block found in fix response for {contact_id}")


def _parse_output(response: anthropic.types.Message, contact_id: str) -> dict:
    if response.stop_reason == "max_tokens":
        raise OutputParseError(f"max_tokens reached during fix for {contact_id}")

    raw_text = _extract_text(response, contact_id).strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise OutputParseError(f"JSON decode failed during fix for {contact_id}: {exc}") from exc

    missing = [k for k in ALL_KEYS if k not in parsed]
    if missing:
        raise OutputParseError(f"Fix response missing top-level keys for {contact_id}: {missing}")

    for key in EMAIL_KEYS:
        for sub in ("subject", "body"):
            if sub not in parsed[key]:
                raise OutputParseError(f"Fix response missing '{sub}' in {key} for {contact_id}")
    for key in CALL_KEYS:
        if "body" not in parsed[key]:
            raise OutputParseError(f"Fix response missing 'body' in {key} for {contact_id}")

    return parsed


def fix_generated(contact_id: str, research: dict, generated: dict, hard_failures: list) -> dict:
    """Return a corrected 8-key dict, fixing only the deliverables lint flagged.

    Raises agent2_build.OutputParseError (matching the exception type lint.py
    already catches around the old regeneration path) if the fix call cannot
    be parsed within the retry budgets.
    """
    keys_to_fix = _keys_to_fix(hard_failures)
    user_message = _build_user_message(
        research["brief_text"], generated, hard_failures, keys_to_fix
    )

    parsed = None
    last_exc = None
    for budget in FIX_MAX_TOKENS_BUDGETS:
        response = _call_fix(user_message, budget)
        try:
            parsed = _parse_output(response, contact_id)
            last_exc = None
            break
        except OutputParseError as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc

    # Belt-and-braces: force every key the model wasn't asked to touch back
    # to its pre-fix value, regardless of what it actually returned. This is
    # what actually prevents a passing deliverable from regressing — the
    # instruction in the prompt is necessary but not sufficient.
    for key in ALL_KEYS:
        if key not in keys_to_fix:
            parsed[key] = generated[key]

    return parsed
