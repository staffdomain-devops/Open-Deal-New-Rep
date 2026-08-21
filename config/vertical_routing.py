"""
Vertical routing table for the Lane A Owner-Changed Re-Engagement Pipeline.

Provides route(industry) -> VerticalRoute for Phase 3 (Exclusion & Routing).
14 rows: 13 named verticals + 1 default (Bells Pure Ice, tokens=[]).

Source: SD_Reengagement_LaneA_Build_Spec.md §4
"""

from dataclasses import dataclass
from typing import List, Dict, Any


@dataclass(frozen=True)
class VerticalRoute:
    """Result of a vertical routing lookup."""
    case_study: str
    email3_url: str
    email4_url: str


# 13 named rows followed by the default row (tokens=[]).
# Order matters: first-hit-wins top-to-bottom.
ROUTING_TABLE: List[Dict[str, Any]] = [
    {
        "tokens": ["accounting", "bookkeep", "tax"],
        "case_study": "SJM Accountants (accounting firm)",
        "email3_url": "https://www.staffdomain.com/accounting-finance/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
    {
        "tokens": ["staffing", "recruit", "human resources", "executive search"],
        "case_study": "Cox Purtell (recruitment)",
        "email3_url": "https://www.staffdomain.com/solutions/recruitment/",
        "email4_url": "https://www.staffdomain.com/build-your-team/hr-recruitment/",
    },
    {
        "tokens": [
            "it services",
            "information technology",
            "computer software",
            "software",
            "computer network",
            "cyber",
            "telecommunications",
            "technology",
        ],
        "case_study": "Systemnet (Sydney MSP)",
        "email3_url": "https://www.staffdomain.com/technology/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
    {
        "tokens": [
            "construction",
            "civil engineering",
            "building",
            "architecture",
            "engineering",
            "joinery",
            "design",
        ],
        "case_study": "Carrera by Design (construction/joinery)",
        "email3_url": "https://www.staffdomain.com/construction-engineering/",
        "email4_url": "https://www.staffdomain.com/build-your-team/construction-support/",
    },
    {
        "tokens": ["hospital", "health", "medical", "care", "wellness"],
        "case_study": "Verus (healthcare)",
        "email3_url": "https://www.staffdomain.com/health-care/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
    {
        "tokens": ["law", "legal"],
        "case_study": "Elias Gates (legal)",
        "email3_url": "https://www.staffdomain.com/professional-services/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
    {
        "tokens": [
            "marketing",
            "advertising",
            "public relations",
            "graphic design",
            "media",
        ],
        "case_study": "Capital-E (marketing and events)",
        "email3_url": "https://www.staffdomain.com/professional-services/",
        "email4_url": "https://www.staffdomain.com/build-your-team/sales-marketing/",
    },
    {
        "tokens": ["real estate", "property", "leasing"],
        "case_study": "Capital-E (finance team)",
        "email3_url": "https://www.staffdomain.com/real-estate/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
    {
        "tokens": [
            "financial services",
            "insurance",
            "investment",
            "banking",
            "capital markets",
        ],
        "case_study": "Durst Industries (accounting)",
        "email3_url": "https://www.staffdomain.com/accounting-finance/",
        "email4_url": "https://www.staffdomain.com/solutions/",
    },
    {
        "tokens": ["consulting", "professional training", "business services"],
        "case_study": "Interlinked (professional services)",
        "email3_url": "https://www.staffdomain.com/professional-services/",
        "email4_url": "https://www.staffdomain.com/solutions/",
    },
    {
        "tokens": [
            "logistics",
            "transportation",
            "supply chain",
            "warehousing",
            "maritime",
        ],
        "case_study": "Liftango (logistics)",
        "email3_url": "https://www.staffdomain.com/industries/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
    {
        "tokens": [
            "retail",
            "e-commerce",
            "consumer",
            "hospitality",
            "food",
            "leisure",
        ],
        "case_study": "EatFirst (customer service)",
        "email3_url": "https://www.staffdomain.com/build-your-team/customer-service/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
    {
        "tokens": [
            "manufactur",
            "machinery",
            "mining",
            "industrial",
            "wholesale",
            "automotive",
            "utilities",
            "oil and gas",
            "chemical",
            "printing",
        ],
        "case_study": "Bells Pure Ice (manufacturing)",
        "email3_url": "https://www.staffdomain.com/industries/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
    # Default row — no tokens; catches empty/None and all unmatched industries.
    {
        "tokens": [],
        "case_study": "Bells Pure Ice (manufacturing)",
        "email3_url": "https://www.staffdomain.com/industries/",
        "email4_url": "https://www.staffdomain.com/build-your-team/",
    },
]


def route(industry: str) -> VerticalRoute:
    """Return the VerticalRoute for a given industry string.

    Matching is case-insensitive substring: any token in a row that appears
    anywhere in the normalised industry string wins (first row wins).

    Args:
        industry: The contact's industry property value. None or empty string
                  both fall through to the default row without raising.

    Returns:
        VerticalRoute with case_study, email3_url, email4_url.
    """
    normalised = industry.lower().strip() if industry else ""

    for row in ROUTING_TABLE:
        tokens = row["tokens"]
        if not tokens:
            # Default row — always matches.
            return VerticalRoute(
                case_study=row["case_study"],
                email3_url=row["email3_url"],
                email4_url=row["email4_url"],
            )
        for token in tokens:
            if token in normalised:
                return VerticalRoute(
                    case_study=row["case_study"],
                    email3_url=row["email3_url"],
                    email4_url=row["email4_url"],
                )

    # Unreachable: the default row (tokens=[]) always fires before we exhaust
    # the table. Guard clause for static analysis.
    last = ROUTING_TABLE[-1]
    return VerticalRoute(
        case_study=last["case_study"],
        email3_url=last["email3_url"],
        email4_url=last["email4_url"],
    )
