"""
Close bank for the Lane A Owner-Changed Re-Engagement Pipeline.

Provides assign_close(touches, jobtitle, industry_match_strength) -> (int, str).
Five options locked to JP's exact wording (spec §5.3). Assignment priority from spec §3.7.

Source: SD_Reengagement_LaneA_Build_Spec.md §3.7 + §5.3
"""

from typing import Tuple


# Five close options — JP's exact wording. Do NOT paraphrase or alter punctuation.
CLOSE_BANK: dict[int, str] = {
    1: "Just getting across it all at the moment, and wanted to introduce myself. I'm sure our paths will cross at some stage.",
    2: "Still working my way through the history at the moment, so this is a hello more than anything. No doubt we'll speak properly down the track.",
    3: "Mostly wanted to make sure the handover didn't happen quietly without you knowing. I expect we'll cross paths before long.",
    4: "Nothing more to this than putting a name to the new face on your account. We'll catch up properly at some stage down the track, I'm sure.",
    5: "Getting my bearings on everything at the moment more than anything else. Once I'm across it all properly we can have more of a chat when the time suits.",
}

# Module-level rotation counter. Stored as a single-element list so the function
# can mutate it without the `global` keyword.
_close_counter: list[int] = [0]

# C-suite title tokens for option 4 assignment (case-insensitive substring match).
_CSUITE_TOKENS = ["CEO", "MD", "COO", "CFO", "Partner", "Principal", "Director"]


def assign_close(
    touches: int,
    jobtitle: str,
    industry_match_strength: str,
) -> Tuple[int, str]:
    """Return (option_number, close_text) for a contact.

    Priority (highest first):
        1. touches >= 30  -> option 3
        2. C-suite title  -> option 4 (substring match, case-insensitive)
        3. exact industry match -> option 5
        4. fallback: rotate 1 -> 2 -> 1 -> 2

    Args:
        touches: Number of contact touches (e.g. num_contacted_notes).
        jobtitle: The contact's job title string.
        industry_match_strength: "exact" or "partial" (from the routing stage).

    Returns:
        (option_number, close_text) tuple.
    """
    # Priority 1: heavy touch override
    if touches >= 30:
        return (3, CLOSE_BANK[3])

    # Priority 2: C-suite title (case-insensitive substring)
    jobtitle_lower = (jobtitle or "").lower()
    for token in _CSUITE_TOKENS:
        if token.lower() in jobtitle_lower:
            return (4, CLOSE_BANK[4])

    # Priority 3: strong (exact) industry match
    if industry_match_strength == "exact":
        return (5, CLOSE_BANK[5])

    # Fallback: rotate between options 1 and 2
    # _close_counter[0] == 0 -> option 1; == 1 -> option 2
    option_number = 1 + _close_counter[0]  # 0 -> 1, 1 -> 2
    _close_counter[0] = (_close_counter[0] + 1) % 2
    return (option_number, CLOSE_BANK[option_number])
