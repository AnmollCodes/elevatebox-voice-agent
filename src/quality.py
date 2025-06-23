"""
Call quality scorer — evaluating the agent's performance, not just the lead's intent.

The classifier and trajectory modules tell us *what the lead did*. This module tells
us *what the agent did* — and whether it was good sales technique.

Scoring rubric (100 points total):
  - Qualifying questions     30 pts   Did Priya ask about budget, products, timeline, features?
  - Objection handling       25 pts   When the lead raised a concern, did Priya address it?
  - Language mirroring       20 pts   Did Priya use the same language (HI/TE/EN) the lead used?
  - Call structure           15 pts   Opening hook → discovery → pitch → close → next step
  - Urgency / CTA            10 pts   Did Priya create urgency or give a clear next step?

Each dimension returns a sub-score and a coaching note. The overall QualityScore
is the weighted sum. Scores <40 are flagged for immediate re-training; 40–70 need
coaching; >70 are good; >90 are examples worth sharing with the team.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------

# Qualifying questions Priya should ask (any variant counts)
_QUALIFYING_PATTERNS: list[tuple[str, str]] = [
    (r"kitne products|how many products|product count|product range|kaun se products",
     "products question"),
    (r"budget|kitna (invest|spend|lagaoge)|how much (can you|are you)|₹|rs\.?\s*\d",
     "budget question"),
    (r"kab (chahiye|start)|when (do you|would you|can we) start|timeline|deadline|delivery",
     "timeline question"),
    (r"(any )?feature(s)?|functionality|payment gateway|cod|cod support|delivery|shipping",
     "features question"),
    (r"currently (selling|using)|pehle se (hai|use)|existing (website|store)|already (have|selling)",
     "current situation question"),
    (r"target (customer|audience|market)|kaun kharidega|who (buys|is your customer)",
     "target audience question"),
]

# Objection-handling patterns — Priya should respond to concerns, not ignore them
_OBJECTION_RESPONSE_PATTERNS: list[str] = [
    r"samajh (sakta|sakti) hoon|i understand|totally (get|understand|fair)",
    r"no problem|bilkul|of course|sure sure",
    r"let me (explain|clarify|show)|main batata|main bataati",
    r"actually|in fact|the thing is|asal mein",
    r"(we have|hamare paas)|our (price|package|plan) (includes|starts|is)",
]

# Language signals — if lead uses Hindi/Telugu, Priya should too
_HINDI_SIGNALS = r"haan|nahi|kya|hai|ho|aap|mujhe|kitna|abhi|karo|karte|chahiye|lagega|baat"
_TELUGU_SIGNALS = r"cheppandi|cheyandi|undi|ledu|ayindi|avutundi|cheyagalaru|meeru|nenu"
_ENGLISH_SIGNALS = r"i want|we need|can you|how much|when can|please|thank"

# Call structure phases (in order of expected appearance)
_PHASES: list[tuple[str, str]] = [
    (r"hi|hello|namaste|namaskar|good (morning|afternoon|evening)", "greeting"),
    (r"online (store|shop|website)|e-commerce|website (banana|build|develop)", "pitch hook"),
    (r"tell me about|apke baare mein|your (business|products|store)", "discovery"),
    (r"hamare (paas|package)|we offer|our solution|features include", "solution pitch"),
    (r"(next step|aage|shall we|let'?s|move forward|start|payment|plan)", "close/CTA"),
]

# Urgency / CTA patterns
_CTA_PATTERNS: list[str] = [
    r"today|aaj|abhi|right now|don'?t wait|limited (time|offer|slots)",
    r"shall we (start|proceed|begin)|aage badhein|let'?s (go|do it|move)",
    r"i'?ll send|main bhejta|details (aa rahe|coming)|checking (now|kar raha)",
    r"call you (back|tomorrow|later)|callback (schedule|book|set)",
    r"whatsapp (pe|par|on)|send (on|to) whatsapp|message kar",
]


def _score_qualifying(agent_text: str) -> tuple[int, list[str]]:
    """Return (score 0-30, list of coaching notes)."""
    asked = []
    missed = []
    for pattern, label in _QUALIFYING_PATTERNS:
        if re.search(pattern, agent_text, re.IGNORECASE):
            asked.append(label)
        else:
            missed.append(label)

    # 5 pts per question asked, max 30
    score = min(30, len(asked) * 5)
    notes = []
    if len(asked) >= 5:
        notes.append("Excellent qualifying — covered budget, timeline, products, and more.")
    elif len(asked) >= 3:
        notes.append(f"Good qualifying ({len(asked)}/6 questions). Missed: {', '.join(missed[:2])}.")
    elif len(asked) >= 1:
        notes.append(f"Weak qualifying — only asked about {', '.join(asked)}. Add budget & timeline.")
    else:
        notes.append("No qualifying questions detected — Priya should open with discovery.")
    return score, notes


def _score_objection_handling(full_text: str, agent_text: str) -> tuple[int, list[str]]:
    """
    Check: if the lead raised objections, did Priya respond with empathy/solution?
    Returns (score 0-25, coaching notes).
    """
    # Detect if lead had objections
    lead_objections = re.search(
        r"not interested|too expensive|bahut mehnga|busy|not now|abhi nahi|"
        r"already have|dusra|budget nahi|nahi chahiye",
        full_text, re.IGNORECASE
    )
    if not lead_objections:
        # No objections raised — full marks, note it
        return 25, ["Lead raised no major objections — maintain this approach."]

    # Lead had objections — did Priya respond with handling phrases?
    responses_found = sum(
        1 for p in _OBJECTION_RESPONSE_PATTERNS
        if re.search(p, agent_text, re.IGNORECASE)
    )
    score = min(25, responses_found * 8)
    if responses_found >= 3:
        return score, ["Strong objection handling — empathy + solution clearly articulated."]
    if responses_found >= 1:
        return score, ["Partial objection handling — Priya acknowledged concern but lacked resolution."]
    return 0, ["⚠ Objection handling missing — lead raised concerns that Priya ignored."]


def _score_language_mirror(lead_text: str, agent_text: str) -> tuple[int, list[str]]:
    """
    Did Priya use the same language the lead used?
    Returns (score 0-20, coaching notes).
    """
    lead_hindi   = bool(re.search(_HINDI_SIGNALS,   lead_text, re.IGNORECASE))
    lead_telugu  = bool(re.search(_TELUGU_SIGNALS,  lead_text, re.IGNORECASE))
    agent_hindi  = bool(re.search(_HINDI_SIGNALS,   agent_text, re.IGNORECASE))
    agent_telugu = bool(re.search(_TELUGU_SIGNALS,  agent_text, re.IGNORECASE))

    notes = []
    score = 0

    if lead_hindi and agent_hindi:
        score += 10
        notes.append("Hindi mirroring detected — lead felt at home.")
    elif lead_hindi and not agent_hindi:
        notes.append("⚠ Lead spoke Hindi but Priya didn't mirror — use Hindi phrases.")

    if lead_telugu and agent_telugu:
        score += 10
        notes.append("Telugu mirroring detected — excellent local rapport.")
    elif lead_telugu and not agent_telugu:
        notes.append("⚠ Lead used Telugu but Priya didn't mirror — add Telugu phrases.")

    # English: always present, award points for consistent presence
    agent_english = bool(re.search(_ENGLISH_SIGNALS, agent_text, re.IGNORECASE))
    if agent_english:
        score = max(score, 10)  # floor at 10 if English covered

    if not notes:
        notes.append("English-only conversation — consider opening in regional language.")

    return min(20, score), notes


def _score_structure(agent_text: str) -> tuple[int, list[str]]:
    """
    Did Priya follow the expected call structure?
    Returns (score 0-15, coaching notes).
    """
    phases_hit = [label for pattern, label in _PHASES
                  if re.search(pattern, agent_text, re.IGNORECASE)]
    score = min(15, len(phases_hit) * 3)
    if len(phases_hit) >= 4:
        return score, [f"Good call structure — hit {len(phases_hit)}/5 phases."]
    if len(phases_hit) >= 2:
        missing = [lbl for _, lbl in _PHASES if lbl not in phases_hit]
        return score, [f"Partial structure — missed: {', '.join(missing)}."]
    return score, ["⚠ Weak call structure — jump straight to discovery and pitch."]


def _score_cta(agent_text: str) -> tuple[int, list[str]]:
    """
    Did Priya end with a clear CTA or next step?
    Returns (score 0-10, coaching notes).
    """
    cta_found = sum(1 for p in _CTA_PATTERNS
                    if re.search(p, agent_text, re.IGNORECASE))
    if cta_found >= 2:
        return 10, ["Strong CTA — urgency and next step clearly communicated."]
    if cta_found == 1:
        return 6, ["Weak CTA — Priya gave a next step but no urgency. Add 'today only' or similar."]
    return 0, ["⚠ No CTA detected — always close with a specific next step."]


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass
class QualityScore:
    """
    Holistic quality assessment of the agent's side of the call.

    total           0–100 weighted composite score
    grade           A/B/C/D/F human label
    qualifying      Sub-score and notes for qualifying questions (max 30)
    objection       Sub-score and notes for objection handling (max 25)
    language        Sub-score and notes for language mirroring (max 20)
    structure       Sub-score and notes for call structure (max 15)
    cta             Sub-score and notes for CTA / urgency (max 10)
    coaching_notes  All coaching observations combined
    flag            True when score <40 — needs immediate review
    """
    total: int
    grade: str
    qualifying: int
    objection: int
    language: int
    structure: int
    cta: int
    coaching_notes: list[str]
    flag: bool

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "grade": self.grade,
            "flag": self.flag,
            "dimensions": {
                "qualifying_questions": self.qualifying,
                "objection_handling": self.objection,
                "language_mirroring": self.language,
                "call_structure": self.structure,
                "cta_urgency": self.cta,
            },
            "coaching_notes": self.coaching_notes,
        }


def _grade(score: int) -> str:
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


# ---------------------------------------------------------------------------
# Transcript splitter — separate agent lines from lead lines
# ---------------------------------------------------------------------------

def _split_sides(transcript: str) -> tuple[str, str]:
    """
    Split a transcript into (agent_text, lead_text).
    Looks for lines prefixed with 'Agent:', 'Priya:', 'A:' or 'Lead:', 'L:', 'Customer:'.
    Falls back to treating even-numbered lines as agent if no prefixes found.
    """
    agent_lines: list[str] = []
    lead_lines: list[str] = []

    agent_prefix = re.compile(r"^\s*(agent|priya|a)\s*:\s*", re.IGNORECASE)
    lead_prefix  = re.compile(r"^\s*(lead|customer|prospect|l|c)\s*:\s*", re.IGNORECASE)

    for line in transcript.splitlines():
        if agent_prefix.match(line):
            agent_lines.append(agent_prefix.sub("", line))
        elif lead_prefix.match(line):
            lead_lines.append(lead_prefix.sub("", line))

    if not agent_lines and not lead_lines:
        # No prefixes — odd lines = agent, even = lead (rough heuristic)
        lines = [ln for ln in transcript.splitlines() if ln.strip()]
        agent_lines = lines[::2]
        lead_lines  = lines[1::2]

    return " ".join(agent_lines), " ".join(lead_lines)


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def score_call_quality(transcript: str) -> QualityScore:
    """
    Evaluate the quality of the agent's performance in a transcript.

    Parameters
    ----------
    transcript  Full conversation transcript with 'Agent:' / 'Lead:' prefixes.

    Returns
    -------
    QualityScore with total score, grade, dimension breakdown, and coaching notes.
    """
    agent_text, lead_text = _split_sides(transcript)

    q_score, q_notes   = _score_qualifying(agent_text)
    o_score, o_notes   = _score_objection_handling(transcript, agent_text)
    l_score, l_notes   = _score_language_mirror(lead_text, agent_text)
    s_score, s_notes   = _score_structure(agent_text)
    c_score, c_notes   = _score_cta(agent_text)

    total = q_score + o_score + l_score + s_score + c_score
    all_notes = q_notes + o_notes + l_notes + s_notes + c_notes

    return QualityScore(
        total=total,
        grade=_grade(total),
        qualifying=q_score,
        objection=o_score,
        language=l_score,
        structure=s_score,
        cta=c_score,
        coaching_notes=all_notes,
        flag=total < 40,
    )
