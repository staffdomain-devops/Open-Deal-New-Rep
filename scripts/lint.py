"""lint.py — Lint engine for the Lane A re-engagement pipeline (single contact).

Usage:
    python scripts/lint.py <contact_id>
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

from agent2_build import OutputParseError, write_generated
from agent3_fix import fix_generated
from config.call_note_boilerplate import SEQUENCE_LINE
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

# H07: emails are body-only now, no greeting or sign-off (see check_h07).
GREETING_RE = re.compile(r"^\s*(hi|hello|hey)\b", re.IGNORECASE)
SIGNOFF_MARKERS = ("[rep first name]", "kind regards", "cheers", "best regards")

# Required labelled sections (amendment check 14). Two kinds of label are
# deliberately absent:
#   - DO NOT SAY is conditional on the brief carrying sensitive material, and
#     check 16 owns it.
#   - WHY THIS CALL (call notes) and RULES (pin) are supplied by
#     assemble_bodies.py, which runs after this lint, so they are guaranteed by
#     construction rather than checked here. See config/call_note_boilerplate.py.
CALL_LABELS = (
    "WHO", "HISTORY", "HOW IT ENDED", "EMAILS SO FAR", "GOAL", "IF VOICEMAIL",
)
# The pin has its own skeleton (§6.3) and carries no HISTORY label.
PIN_LABELS = ("WHY", "STORY", "HOW IT ENDED", "SEQUENCE")

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

# Matched on word boundaries, not as bare substrings: "bpo" is a substring of
# the real segment company "GBPO Solutions", and stemming "offshor"/"outsourc"
# without a leading boundary would do the same to any word that happens to
# contain them.
OFFSHORE_STEMS = (
    r"offshor\w*", r"outsourc\w*", r"bpo", r"onshore", r"nearshore",
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


def _label_pattern(label: str) -> re.Pattern:
    """Match a labelled section header at the start of a line.

    Tolerates a parenthetical between the label and its colon, because the
    pin's sequence header is `SEQUENCE (enrolled {date}):` when an enrolment
    date is available and a bare `SEQUENCE:` when it is not.
    """
    return re.compile(
        r"^[ \t]*" + re.escape(label) + r"\b[^\n:]*:",
        re.IGNORECASE | re.MULTILINE,
    )


def _has_label(body: str, label: str) -> bool:
    return bool(_label_pattern(label).search(body))


def _label_section(body: str, label: str, all_labels) -> str:
    """Return the text following `label`, up to whichever other label comes next."""
    match = _label_pattern(label).search(body)
    if not match:
        return ""
    start = match.end()
    end = len(body)
    for other in all_labels:
        if other == label:
            continue
        nxt = _label_pattern(other).search(body, start)
        if nxt and nxt.start() < end:
            end = nxt.start()
    return body[start:end]


def _all_email_text(generated: dict) -> str:
    parts = []
    for k in EMAIL_KEYS:
        parts.append(generated[k].get("subject", ""))
        parts.append(generated[k].get("body", ""))
    return " ".join(parts)


def _ngrams(text: str, n: int = 4) -> set:
    words = text.split()
    if len(words) < n:
        return set()
    return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}


def _proper_nouns(text: str) -> list:
    """Capitalised words sitting inside a sentence, not opening one.

    A sentence-initial capital is grammar, not a name. Everything else that is
    capitalised mid-sentence is the kind of token check 12 actually cares
    about: the competitor or provider name Agent 1 was told to quote.
    """
    nouns = []
    sentence_start = True
    for token in re.findall(r"[A-Za-z][A-Za-z'\-]*|[.!?]", text):
        if token in (".", "!", "?"):
            sentence_start = True
            continue
        if not sentence_start and token[:1].isupper() and len(token) >= 3:
            nouns.append(token)
        sentence_start = False
    return nouns


def _boundary_pattern(terms) -> re.Pattern:
    """Compile `terms` into one alternation matched on word boundaries.

    Bare substring matching is what makes a short banned word dangerous: on
    the live segment "mate" hits inside "materials" and "estimate" (Building
    Materials is one of the larger verticals on the list) and "sorted" hits
    the real company name "Sorted Digital Marketing". Every one of those is a
    valid candidate, and a hard failure here removes it from the campaign.
    """
    return re.compile(
        r"\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b",
        re.IGNORECASE,
    )


BANNED_RE = _boundary_pattern(CARICATURE_WORDS + BANNED_PHRASES)
# Already regex fragments, so they are alternated directly rather than escaped.
OFFSHORE_RE = re.compile(
    r"\b(?:" + "|".join(OFFSHORE_STEMS) + r")\b", re.IGNORECASE
)


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


def check_h03(generated: dict, research: dict) -> tuple:
    """Caricature and banned phrases — but not the prospect's own name.

    Word boundaries stop "mate" hitting inside "materials" and "estimate", but
    they cannot help a company that IS one of these words: "Sorted Digital
    Marketing" is on the live segment. Naming the account is correct copy, so
    a hit that is part of that name is exempt, as in H04.
    """
    company_words = {w.lower() for w in research.get("company_name_words", [])}
    for match in BANNED_RE.finditer(_all_email_text(generated)):
        phrase = match.group(0).lower()
        if phrase in company_words:
            continue
        return (True, f"H03: banned phrase '{phrase}' found")
    return (False, "")


def check_h04(generated: dict, research: dict) -> tuple:
    """Offshore vocabulary in the copy — but not the prospect's own name.

    The rule exists to stop OUR copy leaking the word, not to ban naming the
    account. Four contacts on the live segment are the vocabulary: "Australian
    Outsourcing Broker", "Owen Warburton - Offshoring Management Agency",
    "GBPO Solutions", and one whose industry is "Outsourcing Offshoring".
    Writing their own company name back to them is correct, so a hit that is
    part of that name is exempt.
    """
    company_words = {w.lower() for w in research.get("company_name_words", [])}
    for match in OFFSHORE_RE.finditer(_all_email_text(generated)):
        word = match.group(0)
        if word.lower() in company_words:
            continue
        return (True, f"H04: offshore vocab '{word.lower()}' found")
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
    """No greeting or sign-off: emails are body-only.

    Originally required every body to end with the literal '[Rep first
    name]' placeholder. That placeholder (and the "Hi [name]," greeting) is
    no longer wanted at all — the sending system adds both separately — so
    this now checks the opposite: that no greeting or sign-off leaked in.
    """
    for key in EMAIL_KEYS:
        body = generated[key].get("body", "")
        lines = [l for l in body.splitlines() if l.strip()]
        if not lines:
            return (True, f"H07: {key} body is empty")
        if GREETING_RE.match(lines[0]):
            return (True, f"H07: {key} body opens with a greeting ('{lines[0]}'); body-only, no greeting")
        last_lower = lines[-1].strip().lower()
        if any(marker in last_lower for marker in SIGNOFF_MARKERS):
            return (True, f"H07: {key} body ends with a sign-off ('{lines[-1]}'); body-only, no sign-off")
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
    """Name invention — ADVISORY, not a hard failure. See run_lint().

    §7.1 #10 reads "check every capitalised first name". This checks every
    capitalised word, which is a different and much wider net: across the live
    segment, 172 distinct title-cased words appear in job titles alone
    (Director on 267 contacts, Managing and Manager on 102 each). The system
    prompt tells the model to reference the old role and name colleagues by
    title, so "your Operations Manager" is ordinary correct copy that this
    reads as an invented name. Until it matches on names rather than capitals,
    a hard failure here drops valid candidates, so it warns instead.
    """
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
            return (True, f"H10 (advisory): name '{word}' not found in brief")
    return (False, "")


def check_h11(generated: dict) -> tuple:
    combined = _all_email_text(generated).lower()
    if "no agenda" in combined or "no pitch" in combined:
        return (True, "H11: 'no agenda' or 'no pitch' found in emails")
    return (False, "")


def check_h12(generated: dict, research: dict) -> tuple:
    """INTERNAL - NEVER REFERENCE material surfacing in the emails.

    Agent 1 fills `sensitive_items` with exact quoted fragments, a sentence
    each. The old form of this check took the first three tokens of that
    sentence, minus a ten-word stopword list, and failed on any of them
    appearing anywhere in ~500 words of copy. Being positional, it tested
    whichever words happened to open the quote, which are its most generic
    ones: "Budget was cut after their CFO left" yields budget/cut/after, and
    every sequence ever written contains "after".

    What actually constitutes a leak is narrower, so this looks for two things:
      (a) a proper noun from the quote — the competitor or provider name that
          identifies the record — unless it is a name the copy is entitled to
          use anyway (the recipient, a colleague, their company);
      (b) four consecutive words of the quote reproduced in the copy, which is
          quoting or paraphrasing close enough to count.

    Known gap: a proper noun that OPENS a sensitive fragment ("Beepo undercut
    us on price.") is not distinguishable from ordinary sentence-initial
    capitalisation, so (a) misses it unless it also appears mid-sentence. The
    prompt's own ban on naming another provider is the primary control here;
    this check is the net beneath it, not a substitute for it.
    """
    sensitive = research.get("sensitive_items") or []
    if not sensitive:
        return (False, "")

    allowed = {n.lower() for n in research.get("allowed_names", [])}
    email_text = _all_email_text(generated)
    email_grams = _ngrams(email_text.lower())

    for item in sensitive:
        for noun in _proper_nouns(item):
            if noun in COMMON_CAPS or noun.lower() in allowed:
                continue
            if re.search(rf"\b{re.escape(noun)}\b", email_text, re.IGNORECASE):
                return (True, f"H12: INTERNAL name '{noun}' found in emails")
        for gram in _ngrams(item.lower()):
            if gram in email_grams:
                return (True, f"H12: INTERNAL phrase '{gram}' found in emails")
    return (False, "")


# ---------------------------------------------------------------------------
# Hard check functions H13-H18 (v1.1 amendments), plus H19 (local, no spec
# number — see its docstring)
# ---------------------------------------------------------------------------


def _call_note_words(body: str) -> int:
    """Word count of a call note, discounting its TIMEZONE line.

    Checks 13 and 17 pull against each other on exactly the records that need
    both: a tersely-filled US call1 lands on 100 words with the TIMEZONE line
    and 89 without, so charging the note for a label the spec obliges it to
    carry would hard-fail US and UK records on their geography. That is the
    same collision §6.1's caps had with check 14, resolved the same way — the
    caps govern the briefing the model composes, not the mandated scaffolding
    around it. See config/call_note_boilerplate.py.
    """
    section = _label_section(body, "TIMEZONE", CALL_LABELS + ("TIMEZONE",))
    if not section.strip():
        return _count_words(body)
    # +1 for the "TIMEZONE:" label itself, which _label_section excludes.
    return _count_words(body) - _count_words(section) - 1


def check_h13(generated: dict) -> tuple:
    call1_wc = _call_note_words(generated["call1"].get("body", ""))
    call2_wc = _call_note_words(generated["call2"].get("body", ""))
    pin_wc = _count_words(generated["pin"].get("body", ""))
    if call1_wc > CALL_WORD_MAX:
        return (True, f"H13: call1 has {call1_wc} words (max {CALL_WORD_MAX})")
    if call2_wc > CALL_WORD_MAX:
        return (True, f"H13: call2 has {call2_wc} words (max {CALL_WORD_MAX})")
    if pin_wc > PIN_WORD_MAX:
        return (True, f"H13: pin has {pin_wc} words (max {PIN_WORD_MAX})")
    return (False, "")


def check_h14(generated: dict, research: dict) -> tuple:
    for key in ("call1", "call2"):
        body = generated[key].get("body", "")
        for label in CALL_LABELS:
            if not _has_label(body, label):
                return (True, f"H14: {key} missing '{label}' label")

    pin_body = generated["pin"].get("body", "")
    for label in PIN_LABELS:
        if not _has_label(pin_body, label):
            return (True, f"H14: pin missing '{label}' label")

    # The invention detector's inverse: the previous rep is the one name that
    # MUST appear, because it is the caller's only bridge into the relationship.
    prev_rep = (research.get("handover_first_name") or "").strip()
    if prev_rep:
        history = _label_section(generated["call1"].get("body", ""), "HISTORY", CALL_LABELS)
        if prev_rep.lower() not in history.lower():
            return (True, f"H14: call1 HISTORY does not name previous rep '{prev_rep}'")
        why = _label_section(pin_body, "WHY", PIN_LABELS)
        if prev_rep.lower() not in why.lower():
            return (True, f"H14: pin WHY does not name previous rep '{prev_rep}'")
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
    """Amendment check 17: US/UK call notes must carry the TIMEZONE line.

    The rep dialling a US or UK record needs to know when it is reasonable to
    ring; §6.1 and §6.2 both carry the label, §6.3's pin skeleton does not, so
    this looks at call1 and call2 only.

    Absence is the failure. A TIMEZONE line on a record that does not need one
    is not checked: the prompt says US/UK only, but hard-failing a record for
    carrying a line too many would drop a valid candidate over a note the
    prospect never sees.
    """
    geo = research.get("geo", "AU")
    if geo not in ("US", "UK"):
        return (False, "")
    for key in ("call1", "call2"):
        if not _has_label(generated[key].get("body", ""), "TIMEZONE"):
            return (True, f"H17: {key} missing TIMEZONE line for {geo} record")
    return (False, "")


def check_h18(generated: dict) -> tuple:
    section = _label_section(generated["pin"].get("body", ""), "SEQUENCE", PIN_LABELS)
    if not section.strip():
        return (True, "H18: pin body missing SEQUENCE line")
    # Collapse the model's line wrapping before comparing; everything else about
    # the line is fixed.
    actual = " ".join(section.split())
    if actual != SEQUENCE_LINE:
        return (True, f"H18: pin SEQUENCE line is '{actual}', expected '{SEQUENCE_LINE}'")
    return (False, "")


def check_h19(generated: dict, research: dict) -> tuple:
    """Dialect-marked spelling on a US record.

    H19 is a local number, not a spec one. §3.6.3 and the system prompt both
    ban dialect-marked spelling on US records ("say two weeks, never
    fortnight"), but neither §7.1 nor the v1.1 amendments ever turned that
    into a numbered check. It was written as check_h17, which displaced the
    amendment's real check 17 (the TIMEZONE line) — see
    .planning/phases/05-lint-assembly/05-01-PLAN.md line 128, where the two
    are conflated. Both rules are wanted, so they now sit side by side.

    UK records keep British spelling, so this is US-only.
    """
    if research.get("geo", "AU") != "US":
        return (False, "")
    if "fortnight" in _all_email_text(generated).lower():
        return (True, "H19: 'fortnight' found in emails for US record")
    return (False, "")


# ---------------------------------------------------------------------------
# Soft warning functions W01-W04 (W05 cross-contact check dropped — no longer
# meaningful when each contact runs in an isolated invocation). check_h10 runs
# here too, demoted from the hard list; see its docstring.
# ---------------------------------------------------------------------------


def check_w01(generated: dict) -> tuple:
    combined = " ".join(generated[k].get("body", "") for k in EMAIL_KEYS).lower()
    for phrase in TIC_PHRASES:
        count = combined.count(phrase)
        if count > 1:
            return (True, f"W01: tic phrase '{phrase}' used {count} times across sequence")
    return (False, "")


def check_w02(generated: dict) -> tuple:
    seen: dict = {}
    for idx, key in enumerate(EMAIL_KEYS):
        body = generated[key].get("body", "").lower()
        for ng in _ngrams(body):
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
        lambda: check_h03(generated, research),
        lambda: check_h04(generated, research),
        lambda: check_h05(generated),
        lambda: check_h06(generated, research),
        lambda: check_h07(generated),
        lambda: check_h08(generated),
        lambda: check_h09(generated),
        lambda: check_h11(generated),
        lambda: check_h12(generated, research),
        lambda: check_h13(generated),
        lambda: check_h14(generated, research),
        lambda: check_h15(generated),
        lambda: check_h16(generated, research),
        lambda: check_h17(generated, research),
        lambda: check_h18(generated),
        lambda: check_h19(generated, research),
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
        lambda: check_h10(generated, research),
    ]
    for fn in warn_checks:
        warned, reason = fn()
        if warned:
            soft_warnings.append(reason)

    return (hard_failures, soft_warnings)


# ---------------------------------------------------------------------------
# Fix wrapper (Agent 3) — replaces the old blind full-regeneration, which in
# production once "fixed" call2's word count by handing back a call1 that
# was now itself over the cap. Agent 3 is told exactly which checks failed
# and on which deliverable, and the code enforces that everything else comes
# back unchanged regardless of what the model does — see agent3_fix.py.
# ---------------------------------------------------------------------------


def _fix_contact(contact_id: str, research: dict, generated: dict, hard_failures: list) -> dict:
    fixed = fix_generated(contact_id, research, generated, hard_failures)
    write_generated(contact_id, fixed, research)
    return fixed


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
            # Every failure, not just the first: the fix call is driven by
            # the whole list (Agent 3 needs to know every deliverable that's
            # broken), and attempt 1's list is otherwise lost the moment we
            # overwrite `generated`.
            attempt1 = "; ".join(hard_failures)
            print(f"LINT FAIL (attempt 1) {contact_id}: {attempt1}", file=sys.stderr)
            write_dlq(contact_id, "", "lint_attempt1", attempt1, 0)
            try:
                generated = _fix_contact(contact_id, research, generated, hard_failures)
                hard_failures, soft_warnings = run_lint(generated, research)
            except OutputParseError as exc:
                write_dlq(contact_id, "", "lint_fix", str(exc), 0)
                print(f"FIX FAIL {contact_id}: {exc}", file=sys.stderr)
                sys.exit(1)

        if hard_failures:
            joined = "; ".join(hard_failures)
            write_dlq(contact_id, "", "lint_hard_fail", joined, 0)
            print(f"LINT FAIL (attempt 2, flagged) {contact_id}: {joined}", file=sys.stderr)
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
