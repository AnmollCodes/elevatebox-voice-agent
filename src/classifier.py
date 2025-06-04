"""
Lead classification logic.

Classification happens at two levels:
  1. Real-time, LLM-driven — the voice agent detects signals during the call
     and calls send_whatsapp_hot_lead or book_callback via function calls.
  2. Post-call fallback — after the call ends we re-classify from the transcript
     using keyword signals and optional LLM re-scoring. This catches cases where
     the agent did not fire a function call mid-call.

The rule-based signal lists are intentionally simple and multilingual.
Complexity here creates maintenance debt with little gain.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class LeadStatus(str, Enum):
    HOT = "HOT"
    WARM = "WARM"
    COLD = "COLD"
    UNKNOWN = "UNKNOWN"


@dataclass
class LeadProfile:
    """Everything we know about the lead after the call."""

    status: LeadStatus = LeadStatus.UNKNOWN
    customer_name: Optional[str] = None
    business_type: Optional[str] = None
    products: Optional[str] = None
    budget: Optional[str] = None
    timeline: Optional[str] = None
    features: Optional[str] = None
    barrier: Optional[str] = None           # What is blocking a WARM lead
    callback_time: Optional[str] = None     # Raw phrase: "tomorrow morning"
    callback_time_resolved: Optional[str] = None  # ISO-ish: "2026-08-23 10:00 IST"
    language_used: str = "English"
    call_duration_seconds: int = 0
    transcript: str = ""
    mid_call_whatsapp_sent: bool = False


# ---------------------------------------------------------------------------
# Keyword signal banks — each tuple is (phrase, weight)
# Higher weight means stronger signal toward that classification
# ---------------------------------------------------------------------------

_HOT_SIGNALS: list[tuple[str, float]] = [
    # English
    ("how soon can you start", 1.0),
    ("when can you start", 1.0),
    ("send me the details", 0.9),
    ("send me a quote", 0.9),
    ("what is the price", 0.8),
    ("how much does it cost", 0.8),
    ("what is the cost", 0.8),
    ("let's do it", 1.0),
    ("ready to proceed", 1.0),
    ("let's move forward", 1.0),
    ("i want to get started", 1.0),
    ("can we start this week", 1.0),
    ("book it", 1.0),
    ("where do i pay", 1.0),
    # Hindi
    ("kab shuru kar sakte", 1.0),
    ("kitna lagega", 0.8),
    ("details bhejo", 0.9),
    ("price batao", 0.8),
    ("karne do", 1.0),
    ("shuru karte hain", 1.0),
    ("abhi shuru karo", 1.0),
    ("haan kar lete hain", 1.0),
    # Telugu
    ("enduku cheyali", 0.8),
    ("yentha agatundi", 0.8),
    ("details pampu", 0.9),
    ("ippude start cheyandi", 1.0),
    ("cheyandi", 0.7),
]

_WARM_SIGNALS: list[tuple[str, float]] = [
    # English
    ("budget is tight", 0.9),
    ("not sure about the budget", 0.8),
    ("need to discuss", 0.9),
    ("let me check with", 0.9),
    ("my partner decides", 0.9),
    ("my brother handles", 0.9),
    ("not right now", 0.8),
    ("maybe next month", 0.8),
    ("call me back", 0.7),
    ("not the right time", 0.8),
    ("thinking about it", 0.7),
    ("considering", 0.6),
    # Hindi
    ("abhi nahi", 0.8),
    ("sochna padega", 0.8),
    ("budget nahi hai", 0.9),
    ("partner se poochna padega", 0.9),
    ("baad mein", 0.7),
    ("kal baat karte hain", 0.8),
    # Telugu
    ("ippudu kadu", 0.8),
    ("aalochinchali", 0.8),
    ("taruvata cheppandi", 0.7),
]

_COLD_SIGNALS: list[tuple[str, float]] = [
    # English
    ("just checking", 0.8),
    ("not interested", 1.0),
    ("already have a website", 0.9),
    ("no need", 0.9),
    ("don't need it", 0.9),
    ("no budget", 0.8),
    # Hindi
    ("sirf dekh raha tha", 0.8),
    ("zaroorat nahi", 0.9),
    ("nahi chahiye", 0.9),
    ("pehle se hai website", 0.9),
    # Telugu
    ("avasaram ledu", 0.9),
    ("chuddam anipinchindi", 0.7),
]

_HOT_THRESHOLD = 0.7
_COLD_THRESHOLD = 0.7


def classify_from_transcript(transcript: str) -> LeadStatus:
    """
    Rule-based lead classification from call transcript text.

    Sums weighted signal scores for each category, normalises to 0-1,
    and returns the category whose score clears its threshold.
    Falls back to WARM when neither HOT nor COLD is clear — it is always
    safer to treat an ambiguous prospect as interested-but-not-ready.

    Args:
        transcript: Full call transcript as a single string.

    Returns:
        LeadStatus enum value.
    """
    text = transcript.lower()

    hot_score = _score_signals(text, _HOT_SIGNALS)
    warm_score = _score_signals(text, _WARM_SIGNALS)
    cold_score = _score_signals(text, _COLD_SIGNALS)

    logger.debug(
        "Classification scores — hot: %.2f, warm: %.2f, cold: %.2f",
        hot_score, warm_score, cold_score,
    )

    if hot_score >= _HOT_THRESHOLD and hot_score >= warm_score and hot_score >= cold_score:
        return LeadStatus.HOT
    if cold_score >= _COLD_THRESHOLD and cold_score > warm_score:
        return LeadStatus.COLD
    if warm_score > 0 or (hot_score > 0 and hot_score < _HOT_THRESHOLD):
        return LeadStatus.WARM

    # If none of the signals fired at all, the call was very short or off-topic.
    # Default to WARM — never discard a lead without enough signal.
    return LeadStatus.WARM


def _score_signals(text: str, signals: list[tuple[str, float]]) -> float:
    """
    Sum weights of signals that appear in the text, capped at 1.0.

    Args:
        text: Lowercased transcript.
        signals: List of (phrase, weight) tuples.

    Returns:
        Score between 0.0 and 1.0.
    """
    total = sum(weight for phrase, weight in signals if phrase in text)
    return min(total, 1.0)


def extract_budget_from_transcript(transcript: str) -> Optional[str]:
    """
    Best-effort extraction of a budget figure from the transcript.
    Looks for Rupee symbols, lakh/thousand patterns, and numeric ranges.

    Args:
        transcript: Full call transcript.

    Returns:
        Budget string as mentioned, or None if not found.
    """
    import re

    patterns = [
        r"₹[\s]?[\d,]+(?:\s*(?:lakh|thousand|k|L))?",
        r"rs\.?\s*[\d,]+(?:\s*(?:lakh|thousand|k|L))?",
        r"[\d]+\s*(?:lakh|thousand|k|L)\s*(?:rupees?)?",
        r"budget\s+(?:is\s+)?(?:around|about|roughly)?\s*[\d,]+",
    ]
    text_lower = transcript.lower()
    for pattern in patterns:
        match = re.search(pattern, text_lower)
        if match:
            return match.group().strip()
    return None
