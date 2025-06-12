"""
Real-time buyer persona detection for adaptive mid-call coaching.

The problem with generic sales scripts: "We deliver in 2-8 weeks for ₹15k–₹2L"
lands differently depending on who is listening. An executive glances at the ROI
angle and wants to move fast. A budget-constrained shop owner needs to hear the
floor price before they'll engage. An explorer is still orienting and needs
feature breadth. A time-pressured buyer wants to know if it can ship by Friday.

This module detects which kind of buyer is on the call from conversation patterns
and returns a tailored coaching instruction that the LLM injects into its next
response. The result is an agent that feels perceptive — the caller experiences
"this person gets what I need" without being able to articulate why.

Design choices:
  - 4 personas cover ~90% of SMB e-commerce buyer archetypes in the target market
  - Signals are language-aware (English / Hindi / Telugu)
  - Scoring is additive: multiple weak signals beat one strong one, preventing
    a single word from flipping the whole conversation angle
  - Tie-breaking rules encode domain knowledge (budget-constrained > explorer
    when both score equally, because unaddressed cost concern blocks the sale)
  - Returns a plain string instruction, not a data structure, so it slots directly
    into the Vapi function response without transformation
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Persona taxonomy
# ---------------------------------------------------------------------------

class BuyerPersona(str, Enum):
    """
    Four archetypes that cover the vast majority of SMB e-commerce buyers.

    EXECUTIVE     — Decision maker, time-scarce, cares about ROI and credibility.
                    They ask "will this actually work?" not "how does it work?"
    EXPLORER      — Early-stage, evaluating options, needs to understand the
                    product space before committing. Asks many feature questions.
    BUDGET        — Cost is the primary filter. Will buy if the number is right.
                    Blocked by vague pricing.
    TIME_PRESSURED — Has a concrete deadline (festival season, product launch).
                    Timeline certainty > feature richness.
    UNKNOWN       — Insufficient signals to classify. Use default pitch.
    """
    EXECUTIVE = "executive"
    EXPLORER = "explorer"
    BUDGET = "budget_constrained"
    TIME_PRESSURED = "time_pressured"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Persona signals
# Each tuple: (signal_name, phrase, weight)
# Weights are additive across the conversation — repeated signals reinforce
# ---------------------------------------------------------------------------

_EXECUTIVE_SIGNALS: list[tuple[str, str, float]] = [
    # English
    ("Asks ROI", "return on investment", 0.80),
    ("Asks ROI (v2)", "how will this help my business", 0.75),
    ("Asks credibility", "who else have you built for", 0.85),
    ("Asks credibility (v2)", "do you have examples", 0.70),
    ("References team", "my team will handle", 0.75),
    ("References team (v2)", "my manager", 0.70),
    ("Wants fast decision", "can we close this today", 0.90),
    ("Wants fast decision (v2)", "let's decide now", 0.85),
    ("Mentions scale", "we have multiple branches", 0.70),
    ("Mentions scale (v2)", "we're expanding", 0.70),
    ("Time is scarce", "i'm busy right now", 0.60),
    ("Time is scarce (v2)", "make it quick", 0.65),
    # Hindi
    ("Hindi: credibility", "aur kisko banaya hai", 0.85),
    ("Hindi: quick decision", "abhi decide karte hain", 0.90),
    ("Hindi: business impact", "business badega kya", 0.75),
    # Telugu
    ("Telugu: credibility", "inkevvariki chesaru", 0.85),
    ("Telugu: business", "business ki ela help avutundi", 0.75),
]

_EXPLORER_SIGNALS: list[tuple[str, str, float]] = [
    # English
    ("Feature question", "does it support", 0.60),
    ("Feature question (v2)", "can it do", 0.60),
    ("Platform comparison", "better than shopify", 0.70),
    ("Platform comparison (v2)", "compared to woocommerce", 0.70),
    ("Multiple questions", "also what about", 0.55),
    ("Wants to understand", "how does it work", 0.65),
    ("Asks about tech", "what technology", 0.60),
    ("Asks about maintenance", "who maintains it after", 0.65),
    ("Asks about customization", "can we customize", 0.60),
    ("Asks about integrations", "which payment gateways", 0.65),
    ("Just researching", "still exploring", 0.80),
    ("Just researching (v2)", "comparing options", 0.75),
    # Hindi
    ("Hindi: how does it work", "kaise kaam karta hai", 0.65),
    ("Hindi: what features", "kya features hain", 0.60),
    ("Hindi: comparison", "dusron se better hai kya", 0.70),
    # Telugu
    ("Telugu: how does it work", "ela pani chestundi", 0.65),
    ("Telugu: features", "emi features unnai", 0.60),
]

_BUDGET_SIGNALS: list[tuple[str, str, float]] = [
    # English
    ("Asks cost first", "how much does it cost", 0.80),
    ("Price focus", "what's the price", 0.80),
    ("Mentions tight budget", "budget is limited", 0.85),
    ("Mentions tight budget (v2)", "not much budget", 0.85),
    ("Asks for discount", "any discount", 0.75),
    ("Asks for discount (v2)", "can you reduce", 0.70),
    ("Installment interest", "can i pay in parts", 0.80),
    ("Installment interest (v2)", "emi option", 0.85),
    ("Compares to freelancer", "cheaper than this", 0.65),
    ("Compares to freelancer (v2)", "freelancer quotes less", 0.75),
    ("Price before features", "tell me the cost first", 0.90),
    # Hindi
    ("Hindi: how much", "kitna lagega", 0.80),
    ("Hindi: discount", "kuch discount milega", 0.75),
    ("Hindi: installments", "emi pe hoga kya", 0.85),
    ("Hindi: budget tight", "budget kam hai", 0.85),
    ("Hindi: price first", "pehle price batao", 0.90),
    # Telugu
    ("Telugu: how much", "yentha agatundi", 0.80),
    ("Telugu: discount", "discount untunda", 0.75),
    ("Telugu: budget", "budget takkuva", 0.85),
]

_TIME_PRESSURED_SIGNALS: list[tuple[str, str, float]] = [
    # English
    ("Mentions deadline", "need it by", 0.85),
    ("Deadline (v2)", "launch before", 0.85),
    ("Urgent timeline", "as soon as possible", 0.80),
    ("Festival deadline", "diwali", 0.75),
    ("Festival deadline (v2)", "dasara", 0.75),
    ("Festival deadline (v3)", "ugadi", 0.75),
    ("Season deadline", "festive season", 0.80),
    ("Season deadline (v2)", "sale season", 0.75),
    ("Asks exact timeline", "in how many days", 0.70),
    ("Asks exact timeline (v2)", "how many weeks", 0.70),
    ("Wants express", "can you do it faster", 0.80),
    ("Wants express (v2)", "is there a rush option", 0.75),
    # Hindi
    ("Hindi: need by when", "kab tak hoga", 0.80),
    ("Hindi: ASAP", "jaldi chahiye", 0.85),
    ("Hindi: festival deadline", "diwali se pehle", 0.90),
    ("Hindi: how many days", "kitne din mein", 0.70),
    # Telugu
    ("Telugu: how many days", "yenni rojullo", 0.70),
    ("Telugu: need fast", "twaraga kavali", 0.85),
    ("Telugu: festival", "pandaga mundu", 0.90),
]


# ---------------------------------------------------------------------------
# Persona result
# ---------------------------------------------------------------------------

@dataclass
class PersonaResult:
    """
    Detected buyer persona with confidence score and adaptive coaching instruction.

    coaching_instruction is what the LLM injects into its next turn when
    adapt_prompt() is called from a Vapi function response.
    """
    persona: BuyerPersona
    confidence: float            # 0.0 to 1.0
    matched_signals: list[str] = field(default_factory=list)
    coaching_instruction: str = ""

    @property
    def is_confident(self) -> bool:
        """Returns True when we're confident enough to adapt the pitch."""
        return self.confidence >= 0.55

    def __str__(self) -> str:
        return f"{self.persona.value} ({self.confidence:.0%} confidence)"


# ---------------------------------------------------------------------------
# Adaptive pitch templates
# These are injected mid-call as additional LLM instructions.
# They do not replace the system prompt — they extend it for this turn.
# ---------------------------------------------------------------------------

_COACHING_TEMPLATES: dict[BuyerPersona, str] = {
    BuyerPersona.EXECUTIVE: (
        "This caller is an executive or decision-maker. They value speed and ROI. "
        "Lead with business impact: 'More orders, less admin.' "
        "Skip feature details unless asked. Mention one credible client outcome if possible. "
        "Offer a quick path to decision: 'I can send you a one-page summary right now.'"
    ),
    BuyerPersona.EXPLORER: (
        "This caller is in research mode — comparing options and understanding the space. "
        "They need breadth first, depth on request. "
        "Briefly cover what's included: catalog, payments, mobile, WhatsApp integration. "
        "Position us as the team that explains clearly and doesn't disappear post-launch. "
        "Invite them to ask their specific question — don't overwhelm."
    ),
    BuyerPersona.BUDGET: (
        "This caller is primarily driven by cost. Lead with the entry point: "
        "'Our basic store starts at ₹15,000 — live in 2 weeks, all essentials included.' "
        "Mention that the price is all-in, no hidden charges. "
        "If they push back, offer the 3-month payment split before discounting. "
        "Do not drop price without anchoring the value they'd lose by going cheaper."
    ),
    BuyerPersona.TIME_PRESSURED: (
        "This caller has a deadline — possibly festive season or a product launch. "
        "Acknowledge the timeline first: 'We can have your store live in 14 days on our express track.' "
        "Offer to take the project immediately so the clock starts today. "
        "Reassure them: 'We've delivered 3 festival-season stores on time in the last 6 months.'"
    ),
    BuyerPersona.UNKNOWN: (
        "Persona unclear. Continue with discovery: ask one more qualifying question "
        "to understand whether cost, timeline, or business growth is their primary concern."
    ),
}


# ---------------------------------------------------------------------------
# Core detection engine
# ---------------------------------------------------------------------------

def detect_persona(transcript: str) -> PersonaResult:
    """
    Detect buyer persona from a call transcript (or partial transcript).

    Can be called multiple times during a call as the transcript grows.
    Each call scores from the beginning — the result may shift as more
    signals accumulate.

    Args:
        transcript: Full or partial call transcript as a single string.

    Returns:
        PersonaResult with persona, confidence, matched signals, and
        a tailored coaching_instruction for the LLM.
    """
    text = transcript.lower()

    exec_score, exec_signals = _score(text, _EXECUTIVE_SIGNALS)
    expl_score, expl_signals = _score(text, _EXPLORER_SIGNALS)
    budg_score, budg_signals = _score(text, _BUDGET_SIGNALS)
    time_score, time_signals = _score(text, _TIME_PRESSURED_SIGNALS)

    scores = {
        BuyerPersona.EXECUTIVE: exec_score,
        BuyerPersona.EXPLORER: expl_score,
        BuyerPersona.BUDGET: budg_score,
        BuyerPersona.TIME_PRESSURED: time_score,
    }

    signals = {
        BuyerPersona.EXECUTIVE: exec_signals,
        BuyerPersona.EXPLORER: expl_signals,
        BuyerPersona.BUDGET: budg_signals,
        BuyerPersona.TIME_PRESSURED: time_signals,
    }

    # Normalise so highest is 1.0, then apply minimum threshold
    max_score = max(scores.values()) if scores else 0.0
    if max_score < 0.40:
        return PersonaResult(
            persona=BuyerPersona.UNKNOWN,
            confidence=0.30,
            matched_signals=[],
            coaching_instruction=_COACHING_TEMPLATES[BuyerPersona.UNKNOWN],
        )

    # Normalised confidence per persona
    norm = {p: min(s / max(max_score, 1.0), 1.0) for p, s in scores.items()}

    # Tie-breaking: cost concern blocks sale if unaddressed — budget wins ties
    winner = max(scores, key=lambda p: (scores[p], p == BuyerPersona.BUDGET))
    confidence = min(norm[winner], 1.0)

    return PersonaResult(
        persona=winner,
        confidence=round(confidence, 2),
        matched_signals=signals[winner],
        coaching_instruction=_COACHING_TEMPLATES[winner],
    )


def _score(text: str, signals: list[tuple[str, str, float]]) -> tuple[float, list[str]]:
    """
    Score a set of signals against the text.
    Returns (raw_score, matched_signal_names).
    """
    total = 0.0
    matched: list[str] = []
    for name, phrase, weight in signals:
        if phrase in text:
            total += weight
            matched.append(name)
    return total, matched


# ---------------------------------------------------------------------------
# Convenience: build function call response for Vapi
# ---------------------------------------------------------------------------

def build_adapt_prompt_response(transcript: str) -> dict:
    """
    Build the function call result dict that Vapi expects when a
    `detect_buyer_persona` tool call is made mid-conversation.

    The LLM receives the coaching_instruction as the function result and
    uses it to shape its next response, without the caller hearing any
    mechanical hand-off.

    Returns:
        dict with 'result' key containing the instruction string,
        and 'persona_detected' and 'confidence' for logging.
    """
    result = detect_persona(transcript)

    return {
        "result": result.coaching_instruction,
        "persona_detected": result.persona.value,
        "confidence": result.confidence,
        "signals_matched": len(result.matched_signals),
    }


# ---------------------------------------------------------------------------
# Persona → follow-up WhatsApp angle
# Used post-call to customise the closing message
# ---------------------------------------------------------------------------

_FOLLOWUP_ANGLES: dict[BuyerPersona, str] = {
    BuyerPersona.EXECUTIVE: (
        "I noticed you're focused on results — I've attached our architecture "
        "overview so your technical team can review while we talk next steps."
    ),
    BuyerPersona.EXPLORER: (
        "Since you had several questions about features, I've included our "
        "full capability list in the message below — happy to walk through any of it."
    ),
    BuyerPersona.BUDGET: (
        "You mentioned cost is a key factor. Our entry package at ₹15,000 "
        "covers everything you need to go live — no hidden charges, 12-month support included."
    ),
    BuyerPersona.TIME_PRESSURED: (
        "Given your timeline, I want to flag that our express track can have "
        "your store live in 14 days from kickoff. Slots are limited this month."
    ),
    BuyerPersona.UNKNOWN: (
        "I'd love to understand your business a bit better. "
        "Reply here or call back anytime — I'm here to help."
    ),
}


def get_followup_angle(persona: BuyerPersona) -> str:
    """
    Return the WhatsApp follow-up angle tailored to the detected persona.
    Injected into the post-call WhatsApp message body.
    """
    return _FOLLOWUP_ANGLES.get(persona, _FOLLOWUP_ANGLES[BuyerPersona.UNKNOWN])
