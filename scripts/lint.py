"""lint.py — Lint engine for the Lane A re-engagement pipeline (single contact).

Usage:
    python scripts/lint.py <contact_id>
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from agent2_build import (
    MAX_TOKENS_BUDGETS,
    MaxTokensError,
    OutputParseError,
    _call_realtime,
    parse_output,
    write_generated,
)
from utils import write_dlq

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMAIL_KEYS = ["e1", "e2", "e3", "e4", "e5"]
CALL_KEYS = ["call1", "call2", "pin"]
EM_DASH = "—"
URL_REGEX = re.compile(r"https?://\S+")
# Trailing punctuation a URL picks up when woven into a sentence
# ("...at {url}, have a look" / "...at {url}.") is not part of the URL.
URL_TRAILING_PUNCT = ".,!?;:'\")]}"
RUNNER_TEMP = os.environ.get("RUNNER_TEMP", ".")

WORD_COUNT_MIN = 40
WORD_COUNT_MAX = 120
CALL_WORD_MAX = 100
PIN_WORD_MAX = 130

CARICATURE_WORDS = (
    "mob", "have a crack", "been burnt", "no dramas", "no worries", "all good",
    "reckon", "up your alley", "leave you be", "brutal", "silly money", "chew up",
    "grief", "the lot", "got up", "upstairs", "keen", "mate", "heaps", "bloke",
    "arvo", "spot on", "sorted", "flat out", "stuck into", "fair dinkum", "chuck",
    "gonna", "yeah nah", "squiz",
)

BANNED_PHRASES = (
    "just checking in", "i never heard back", "hope this finds you well",
    "grab a time", "walk you through", "no obligation", "tailored plan",
    "reach out", "circle back", "good to reconnect", "worth exploring",
    "no sales pitch", "touching base",
)

OFFSHORE_WORDS = (
    "offshore", "offshoring", "outsourc", "bpo", "onshore", "nearshore",
)

TIC_PHRASES = (
    "a fair few", "playing catchup", "quick one", "worth a read", "fair bit",
)

COMMON_CAPS = frozenset((
    "Hi", "I", "Staff", "Domain", "HubSpot", "AU", "NZ", "US", "UK",
    "Day", "Rep", "Email", "Lane", "INTERNAL", "NEVER", "REFERENCE",
    "HISTORY", "HOW", "IF", "VOICEMAIL", "DO", "NOT", "SAY", "SEQUENCE",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count_words(text: str) -> int:
    return len(text.split())


def _strip_punct(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text).lower()


def _clean_url(url: str) -> str:
    return url.rstrip(URL_TRAILING_PUNCT)


def _all_email_text(generated: dict) -> str:
    parts = []
    for k in EMAIL_KEYS:
        parts.append(generated[k].get("subject", ""))
        parts.append(generated[k].get("body", ""))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Hard check functions H01-H12 (v1.0 spec §7.1)
# Each returns (failed: bool, reason: str)
# ---------------------------------------------------------------------------


def check_h01(generated: dict) -> tuple:
    for key in EMAIL_KEYS:
        if key not in generated:
            return (True, f"H01: missing email key '{key}'")
        for sub in ("subject", "body"):
            if sub not in generated[key]:
                return (True, f"H01: {key} missing '{sub}'")
    for key in CALL_KEYS:
        if key not in generated:
            return (True, f"H01: missing call key '{key}'")
        if "body" not in generated[key]:
            return (True, f"H01: {key} missing 'body'")
    return (False, "")


def check_h02(generated: dict) -> tuple:
    combined = _all_email_text(generated)
    if EM_DASH in combined:
        return (True, "H02: em dash found in output")
    return (False, "")


def check_h03(generated: dict) -> tuple:
    combined = _all_email_text(generated).lower()
    for phrase in CARICATURE_WORDS + BANNED_PHRASES:
        if phrase in combined:
            return (True, f"H03: banned phrase '{phrase}' found")
    return (False, "")


def check_h04(generated: dict) -> tuple:
    combined = _all_email_text(generated).lower()
    for word in OFFSHORE_WORDS:
        if word in combined:
            return (True, f"H04: offshore vocab '{word}' found")
    return (False, "")


def check_h05(generated: dict) -> tuple:
    for key in EMAIL_KEYS:
        subj = generated[key].get("subject", "")
        if subj != subj.lower():
            return (True, f"H05: {key} subject contains uppercase letters")
        if len(subj) > 45:
            return (True, f"H05: {key} subject is {len(subj)} chars (max 45)")
        word_count = len(subj.split())
        if word_count < 2 or word_count > 7:
            return (True, f"H05: {key} subject has {word_count} words (must be 2-7)")
        if subj.strip().lower().startswith("just"):
            return (True, f"H05: {key} subject starts with 'just'")
    return (False, "")


def check_h06(generated: dict, research: dict) -> tuple:
    email3_url = research.get("email3_url", "")
    email4_url = research.get("email4_url", "")
    for key in ("e1", "e2", "e5"):
        urls = URL_REGEX.findall(generated[key].get("body", ""))
        if urls:
            return (True, f"H06: {key} body contains URL(s): {urls[0]}")
    for key, expected_url in [("e3", email3_url), ("e4", email4_url)]:
        urls = URL_REGEX.findall(generated[key].get("body", ""))
        if len(urls) != 1:
            return (True, f"H06: {key} body must contain exactly one URL (found {len(urls)})")
        found_url = _clean_url(urls[0])
        if expected_url and found_url != expected_url:
            return (True, f"H06: {key} URL '{found_url}' does not match brief URL '{expected_url}'")
    return (False, "")


def check_h07(generated: dict) -> tuple:
    for key in EMAIL_KEYS:
        body = generated[key].get("body", "")
        lines = [l for l in body.splitlines() if l.strip()]
        if not lines or lines[-1] != "[Rep first name]":
            last = lines[-1] if lines else "(empty)"
            return (True, f"H07: {key} last non-empty line is '{last}', expected '[Rep first name]'")
    return (False, "")


def check_h08(generated: dict) -> tuple:
    for key in EMAIL_KEYS:
        body = generated[key].get("body", "")
        wc = _count_words(body)
        if wc < WORD_COUNT_MIN or wc > WORD_COUNT_MAX:
            return (True, f"H08: {key} body has {wc} words (must be {WORD_COUNT_MIN}-{WORD_COUNT_MAX})")
    return (False, "")


def check_h09(generated: dict) -> tuple:
    close_text = generated.get("close_text", "")
    if not close_text:
        return (False, "")
    body_stripped = _strip_punct(generated["e1"].get("body", ""))
    close_stripped = _strip_punct(close_text)
    if close_stripped not in body_stripped:
        return (True, "H09: e1 body missing assigned close text (or altered beyond punctuation)")
    return (False, "")


def check_h10(generated: dict, research: dict) -> tuple:
    allowed = set(research.get("allowed_names", []))

    combined_email_text = " ".join(
        generated[k].get("body", "") + " " + generated[k].get("subject", "")
        for k in EMAIL_KEYS
    )
    pattern = re.compile(r"(?<=[a-z]\s)([A-Z][a-z]{1,})")
    for match in pattern.finditer(combined_email_text):
        word = match.group(1)
        if word in COMMON_CAPS:
            continue
        if word.lower() not in allowed:
            return (True, f"H10: name '{word}' not found in brief")
    return (False, "")


def check_h11(generated: dict) -> tuple:
    combined = _all_email_text(generated).lower()
    if "no agenda" in combined or "no pitch" in combined:
        return (True, "H11: 'no agenda' or 'no pitch' found in emails")
    return (False, "")


def check_h12(generated: dict, research: dict) -> tuple:
    sensitive = research.get("sensitive_items") or []
    if not sensitive:
        return (False, "")
    stopwords = {"the", "and", "for", "was", "had", "with", "from", "that", "this", "they"}
    email_text = _all_email_text(generated).lower()
    for item in sensitive:
        tokens = [
            t for t in re.findall(r"\b\w{3,}\b", item.lower())
            if t not in stopwords
        ]
        for token in tokens[:3]:
            if token in email_text:
                return (True, f"H12: INTERNAL token '{token}' found in emails")
    return (False, "")


# ---------------------------------------------------------------------------
# Hard check functions H13-H18 (v1.1 amendments)
# ---------------------------------------------------------------------------


def check_h13(generated: dict) -> tuple:
    call1_wc = _count_words(generated["call1"].get("body", ""))
    call2_wc = _count_words(generated["call2"].get("body", ""))
    pin_wc = _count_words(generated["pin"].get("body", ""))
    if call1_wc > CALL_WORD_MAX:
        return (True, f"H13: call1 has {call1_wc} words (max {CALL_WORD_MAX})")
    if call2_wc > CALL_WORD_MAX:
        return (True, f"H13: call2 has {call2_wc} words (max {CALL_WORD_MAX})")
    if pin_wc > PIN_WORD_MAX:
        return (True, f"H13: pin has {pin_wc} words (max {PIN_WORD_MAX})")
    return (False, "")


def check_h14(generated: dict) -> tuple:
    for key in CALL_KEYS:
        body = generated[key].get("body", "")
        if "history" not in body.lower():
            return (True, f"H14: {key} body missing HISTORY label")
    return (False, "")


def check_h15(generated: dict) -> tuple:
    call1_lower = generated["call1"].get("body", "").lower()
    if "if voicemail" not in call1_lower:
        return (True, "H15: call1 missing IF VOICEMAIL section")
    idx = call1_lower.index("if voicemail")
    voicemail_section = call1_lower[idx:idx + 400]
    if "catch up" in voicemail_section:
        return (True, "H15: call1 IF VOICEMAIL section contains 'catch up'")
    return (False, "")


def check_h16(generated: dict, research: dict) -> tuple:
    sensitive = research.get("sensitive_items") or []
    if not sensitive:
        return (False, "")
    for key in CALL_KEYS:
        if "do not say" in generated[key].get("body", "").lower():
            return (False, "")
    return (True, "H16: sensitive items present but no DO NOT SAY line found in call notes")


def check_h17(generated: dict, research: dict) -> tuple:
    geo = research.get("geo", "AU")
    if geo not in ("US", "UK"):
        return (False, "")
    email_text = _all_email_text(generated).lower()
    if "fortnight" in email_text:
        return (True, f"H17: 'fortnight' found in emails for {geo} record")
    return (False, "")


def check_h18(generated: dict) -> tuple:
    if "sequence" not in generated["pin"].get("body", "").lower():
        return (True, "H18: pin body missing SEQUENCE MAP reference")
    return (False, "")


# ---------------------------------------------------------------------------
# Soft warning functions W01-W04 (W05 cross-contact check dropped — no longer
# meaningful when each contact runs in an isolated invocation)
# ---------------------------------------------------------------------------


def check_w01(generated: dict) -> tuple:
    combined = " ".join(generated[k].get("body", "") for k in EMAIL_KEYS).lower()
    for phrase in TIC_PHRASES:
        count = combined.count(phrase)
        if count > 1:
            return (True, f"W01: tic phrase '{phrase}' used {count} times across sequence")
    return (False, "")


def check_w02(generated: dict) -> tuple:
    def ngrams(text, n=4):
        words = text.split()
        if len(words) < n:
            return set()
        return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}

    seen: dict = {}
    for idx, key in enumerate(EMAIL_KEYS):
        body = generated[key].get("body", "").lower()
        for ng in ngrams(body):
            if ng in seen:
                prev_idx = seen[ng]
                return (True, f"W02: 4-word phrase '{ng}' repeated in e{prev_idx + 1} and e{idx + 1}")
            seen[ng] = idx
    return (False, "")


def check_w03(generated: dict) -> tuple:
    if "?" not in generated["e3"].get("body", ""):
        return (True, "W03: e3 body missing question mark")
    return (False, "")


def check_w04(generated: dict, research: dict) -> tuple:
    for key, url_field in [("e3", "email3_url"), ("e4", "email4_url")]:
        url = research.get(url_field, "")
        if not url:
            continue
        body = generated[key].get("body", "")
        if url not in body:
            continue
        pos = body.index(url)
        end_pos = pos + len(url)
        if pos < 20 or end_pos > len(body) - 20:
            return (True, f"W04: {key} URL is at start or end of body (should be mid-body with lead-in)")
    return (False, "")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_lint(generated: dict, research: dict) -> tuple:
    hard_checks = [
        lambda: check_h01(generated),
        lambda: check_h02(generated),
        lambda: check_h03(generated),
        lambda: check_h04(generated),
        lambda: check_h05(generated),
        lambda: check_h06(generated, research),
        lambda: check_h07(generated),
        lambda: check_h08(generated),
        lambda: check_h09(generated),
        lambda: check_h10(generated, research),
        lambda: check_h11(generated),
        lambda: check_h12(generated, research),
        lambda: check_h13(generated),
        lambda: check_h14(generated),
        lambda: check_h15(generated),
        lambda: check_h16(generated, research),
        lambda: check_h17(generated, research),
        lambda: check_h18(generated),
    ]

    hard_failures = []
    for fn in hard_checks:
        failed, reason = fn()
        if failed:
            hard_failures.append(reason)

    soft_warnings = []
    warn_checks = [
        lambda: check_w01(generated),
        lambda: check_w02(generated),
        lambda: check_w03(generated),
        lambda: check_w04(generated, research),
    ]
    for fn in warn_checks:
        warned, reason = fn()
        if warned:
            soft_warnings.append(reason)

    return (hard_failures, soft_warnings)


# ---------------------------------------------------------------------------
# Regeneration wrapper
# ---------------------------------------------------------------------------


def _regenerate_contact(contact_id: str, research: dict) -> dict:
    response = _call_realtime(research["brief_text"], MAX_TOKENS_BUDGETS[0])
    parsed = parse_output(response, contact_id)
    write_generated(contact_id, parsed, research)
    return parsed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    contact_id = sys.argv[1] if len(sys.argv) >= 2 else os.environ.get("INPUT_CONTACT_ID")
    if not contact_id:
        print("ERROR: contact_id not provided (argv[1] or INPUT_CONTACT_ID).", file=sys.stderr)
        sys.exit(1)

    gen_path = os.path.join(RUNNER_TEMP, f"generated_{contact_id}.json")
    research_path = os.path.join(RUNNER_TEMP, f"research_{contact_id}.json")

    with open(gen_path, encoding="utf-8") as f:
        generated = json.load(f)
    with open(research_path, encoding="utf-8") as f:
        research = json.load(f)

    write_dlq(contact_id, "", "startup", "sentinel", 0)

    try:
        hard_failures, soft_warnings = run_lint(generated, research)

        if hard_failures:
            print(f"LINT FAIL (attempt 1) {contact_id}: {hard_failures[0]}", file=sys.stderr)
            try:
                generated = _regenerate_contact(contact_id, research)
                hard_failures, soft_warnings = run_lint(generated, research)
            except (MaxTokensError, OutputParseError) as exc:
                write_dlq(contact_id, "", "lint_regenerate", str(exc), 0)
                print(f"REGEN FAIL {contact_id}: {exc}", file=sys.stderr)
                sys.exit(1)

        if hard_failures:
            write_dlq(contact_id, "", "lint_hard_fail", "; ".join(hard_failures), 0)
            print(f"LINT FAIL (attempt 2, flagged) {contact_id}: {hard_failures[0]}", file=sys.stderr)
            sys.exit(1)

        if soft_warnings:
            review_path = os.path.join(RUNNER_TEMP, "review_sample.json")
            entry = {
                "contact_id": contact_id,
                "reasons": soft_warnings,
                "subjects": {k: generated[k].get("subject", "") for k in EMAIL_KEYS},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(review_path, "w", encoding="utf-8") as f:
                json.dump([entry], f, indent=2)
            print(f"Lint passed with soft warnings: {soft_warnings}")
        else:
            print(f"Lint passed cleanly for {contact_id}")

    except SystemExit:
        raise
    except Exception as exc:
        write_dlq(contact_id, "", "lint_unexpected", str(exc), 0)
        print(f"ERROR lint {contact_id}: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
