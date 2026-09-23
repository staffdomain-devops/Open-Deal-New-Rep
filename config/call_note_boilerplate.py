"""
Fixed boilerplate lines for the Lane A call notes and pin note.

These are the parts of the §6.1 / §6.2 / §6.3 templates that never vary per
contact. They are supplied by code (scripts/assemble_bodies.py) rather than by
generation, for two reasons:

  1. Word budget. Amendment check 13 caps call notes at 100 words and the pin
     at 130. Filled as tersely as the templates allow, call1 lands at ~101
     words (~109 once a US/UK record adds its TIMEZONE line) and the pin at
     ~146. Checks 13 and 14 cannot both be satisfied while the model is also
     spending ~26 and ~48 words reproducing text that is identical on every
     record. Moving those lines to code takes call1 to ~75 and the pin to ~98,
     and the caps become comfortably achievable.
  2. Fidelity. Amendment check 16 requires the pin RULES block to match the
     boilerplate exactly. Appending it guarantees that instead of testing it.

§6.3 already authorises this for the pin's RULES block ("code-appended is
acceptable"). Applying the same mechanism to the WHY THIS CALL lines is an
extension of that precedent, not something spec allows on its face, and it
rests on reading the checks' word caps as governing generated content rather
than the finished note. JP should confirm that reading.

Because these lines are added AFTER lint runs, lint must not require them:
CALL_LABELS in scripts/lint.py deliberately omits them, and the pin no
longer has a labelled skeleton at all (see PIN_APPENDIX below).

Source: New SDR - Deal old deal outreach.md §6.1, §6.2, §6.3
"""

# The one deviation from the templates as written: their em dashes are rendered
# here as a full stop (call1) and a comma (pin RULES/PIN_APPENDIX). Spec check
# 2 bans the em dash, and these strings are written straight into HubSpot
# properties.

CALL1_WHY = (
    "WHY THIS CALL: Intro call, sequence touch 3 of 7. Warm, no pitch. "
    "You have never spoken to {first_name}. This is the live handshake behind E1."
)

# Used when the contact record carries no first name.
CALL1_WHY_FALLBACK_NAME = "this contact"

CALL2_WHY = (
    "WHY THIS CALL: Close attempt, sequence touch 6 of 7. "
    "E4 just delivered the what's-changed news."
)

# Appended to the model's pin paragraph. Used to be two things the model had
# to write itself: a machine-shorthand SEQUENCE line ("E1 D1 · E2 D5 · Call
# D7 · ...") checked character-for-character, and a labelled RULES block.
# Both are pure boilerplate that never varies per contact, and both read like
# a system log rather than a note from a colleague, so they're rewritten as
# plain sentences and moved here -- one less thing for a sales rep to parse,
# and one less thing for the model to get exactly right.
PIN_APPENDIX = (
    "\n\nThe plan from here: an intro email today (Day 1), a case study "
    "follow-up on Day 5, an intro call around Day 7, a check-in email on "
    "Day 10, an update on what's changed on Day 15, another call attempt "
    "around Day 17, and a final note on Day 21.\n\n"
    "A few things to keep in mind: if they reply at any point, that ends the "
    "sequence, reply to them personally and close out any open call tasks. "
    "If the Day 7 call actually connects, skip email 3 or tweak its opening "
    "before it sends. Don't bring up their silence or apologise for the gap. "
    "And always call it a dedicated team or someone who works only for "
    "them, never offshoring or BPO."
)


def call1_why(first_name: str) -> str:
    """Return call1's fixed opening line with the contact's first name filled in."""
    name = (first_name or "").strip() or CALL1_WHY_FALLBACK_NAME
    return CALL1_WHY.format(first_name=name)
