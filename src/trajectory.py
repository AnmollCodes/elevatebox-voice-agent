"""
Sentiment trajectory analysis — the arc of a conversation matters as much as its end state.

A lead who opens COLD ("not interested, busy") but closes HOT ("ok send me details,
how do I pay") is more valuable than one who was HOT from the first word. The closing
momentum signals genuine intent; the cold open makes the win harder-earned and more real.

This module splits every transcript into thirds, scores each third independently using
the same signal bank as the main classifier, and returns:
  - per-third scores  (list of 3 floats, 0.0 = fully cold, 1.0 = fully hot)
  - arc label         e.g. "cold→warm→hot"
  - momentum          +1.0 (rising strongly) to −1.0 (falling strongly)
  - turning_point     which third the biggest shift happened in, or None

Architecture note: we re-use the evidence weights from intelligence.py rather than
maintaining a separate signal bank. The only difference is we score sub-segments
rather than the whole transcript, so the absolute values are lower — we normalise
by segment length rather than total transcript length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Signal bank — same spirit as intelligence.py, kept local for independence
# ---------------------------------------------------------------------------

# (pattern, hot_weight, cold_weight)  — patterns scored against each third
_SIGNALS: list[tuple[str, float, float]] = [
    # HOT signals
    (r"let'?s do it|chalo karte hain|abhi shuru|move forward|i'?m in", 1.0, 0.0),
    (r"how (do i |can i )?pay|payment|price|kitna (lagega|hai)|cost", 0.7, 0.0),
    (r"send (me |it )?details|send (it )?on whatsapp|share (the )?link", 0.8, 0.0),
    (r"when (can you|will it|do you) start|kab start|timeline|delivery", 0.6, 0.0),
    (r"i want (to|this)|mujhe chahiye|interested", 0.7, 0.0),
    (r"yes|haan|bilkul|absolutely|definitely|sure|ok ok", 0.4, 0.0),
    (r"call me (tomorrow|today|tonight|kal)|let'?s (talk|meet|connect)", 0.5, 0.0),
    # WARM signals
    (r"maybe|possibly|sochta hoon|think about it|consider", 0.0, 0.0),   # neutral
    (r"next (month|week)|baad mein|later|thoda time", 0.0, 0.2),
    (r"budget (nahi|kam)|not sure (about )?budget|tight budget", 0.0, 0.3),
    # COLD signals
    (r"not interested|interested nahi|mujhe nahi chahiye", 0.0, 1.0),
    (r"don'?t call|mat karo call|please remove|stop calling", 0.0, 1.0),
    (r"busy|abhi nahi|right now no|no time", 0.0, 0.5),
    (r"already have|already using|dusra hai", 0.0, 0.6),
    (r"too expensive|bahut mehnga|can'?t afford", 0.0, 0.7),
]


def _score_segment(text: str) -> float:
    """
    Return a sentiment score for a segment of transcript.
    0.0 = purely cold/negative, 1.0 = purely hot/positive, 0.5 = neutral/mixed.
    """
    text_lower = text.lower()
    hot_total = 0.0
    cold_total = 0.0

    for pattern, hw, cw in _SIGNALS:
        if re.search(pattern, text_lower):
            hot_total += hw
            cold_total += cw

    total = hot_total + cold_total
    if total == 0:
        return 0.5  # neutral — no signals either way
    return hot_total / total


def _score_to_label(score: float) -> str:
    if score >= 0.65:
        return "hot"
    if score >= 0.40:
        return "warm"
    return "cold"


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

@dataclass
class SentimentArc:
    """
    The emotional trajectory of a sales conversation across three acts.

    Attributes
    ----------
    scores          Raw [0,1] sentiment score for each third of the transcript.
                    Index 0 = opening, 1 = middle, 2 = closing.
    labels          Human-readable label per third: "cold" | "warm" | "hot".
    arc             Shorthand string, e.g. "cold→warm→hot".
    momentum        Change from opening to closing, range [−1, +1].
                    Positive = lead warmed up, negative = lead cooled off.
    turning_point   1-indexed third where the biggest single-step shift occurred.
                    None if the conversation was flat throughout.
    coaching_note   One-sentence coaching observation about the arc.
    segment_texts   The three raw text segments (useful for debugging).
    """

    scores: list[float]
    labels: list[str]
    arc: str
    momentum: float
    turning_point: Optional[int]
    coaching_note: str
    segment_texts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "scores": [round(s, 3) for s in self.scores],
            "labels": self.labels,
            "arc": self.arc,
            "momentum": round(self.momentum, 3),
            "turning_point": self.turning_point,
            "coaching_note": self.coaching_note,
        }


# ---------------------------------------------------------------------------
# Coaching observations keyed by arc pattern
# ---------------------------------------------------------------------------

_COACHING: dict[str, str] = {
    "cold→cold→hot":   "Late bloomer — Priya's closing technique worked; open stronger next time.",
    "cold→warm→hot":   "Classic recovery arc — strong close. Reinforce the mid-call pivot tactics.",
    "warm→warm→hot":   "Steady build to a close — reliable pattern. Maintain this pacing.",
    "hot→hot→hot":     "Pre-sold lead — keep the call short and confirm quickly.",
    "cold→cold→cold":  "Flat rejection — review opening hook and qualifying questions.",
    "hot→warm→cold":   "Lost the lead mid-call — check if price or timeline caused the drop.",
    "warm→cold→cold":  "Early promise, then stall — objection likely missed in the middle.",
    "cold→hot→hot":    "Fast turnaround in the opening — great hook. Replicate this.",
    "warm→warm→warm":  "Lukewarm throughout — needed a stronger ask or urgency trigger.",
    "hot→warm→warm":   "Started hot but drifted — closing didn't capitalise on early enthusiasm.",
    "warm→hot→warm":   "Mid-call spike that didn't hold — ask for the commitment at the peak.",
    "cold→warm→warm":  "Improving but no close — one more push at the end would have converted.",
    "hot→hot→warm":    "Close to a win, cooled at the end — pricing or next-step wasn't clear enough.",
    "hot→cold→hot":    "V-shape recovery — lead had a concern in the middle; Priya recovered well.",
    "warm→cold→hot":   "Dip then surge — likely an objection handled successfully. Document it.",
    "cold→cold→warm":  "Slight warming but not enough — follow-up is critical here.",
}


def _coaching_for(arc: str) -> str:
    if arc in _COACHING:
        return _COACHING[arc]
    # fallback by momentum direction
    parts = arc.split("→")
    if parts[-1] == "hot":
        return "Positive close — lead finished warm or hot. Good follow-up timing is key."
    if parts[-1] == "cold":
        return "Negative close — lead disengaged. Consider a longer nurture sequence."
    return "Mixed signals throughout — review transcript for missed objection handling."


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def analyse_trajectory(transcript: str) -> SentimentArc:
    """
    Split transcript into three equal-length segments and score each.

    Parameters
    ----------
    transcript  Full conversation transcript as a single string.

    Returns
    -------
    SentimentArc with scores, labels, arc string, momentum, turning point,
    and a coaching observation.
    """
    # Strip leading/trailing whitespace; protect against empty input
    text = transcript.strip()
    if not text:
        return SentimentArc(
            scores=[0.5, 0.5, 0.5],
            labels=["warm", "warm", "warm"],
            arc="warm→warm→warm",
            momentum=0.0,
            turning_point=None,
            coaching_note="Empty transcript — no analysis possible.",
            segment_texts=["", "", ""],
        )

    # Split into thirds by character count (word boundaries would be better
    # but char-split is deterministic and fast; at this length it doesn't matter)
    n = len(text)
    third = n // 3
    segments = [
        text[:third],
        text[third: 2 * third],
        text[2 * third:],
    ]

    scores = [_score_segment(s) for s in segments]
    labels = [_score_to_label(s) for s in scores]
    arc = "→".join(labels)
    momentum = round(scores[2] - scores[0], 3)

    # Turning point: which transition had the biggest absolute delta?
    deltas = [abs(scores[1] - scores[0]), abs(scores[2] - scores[1])]
    max_delta = max(deltas)
    if max_delta < 0.10:
        turning_point = None  # flat — no meaningful shift
    else:
        # delta[0] = shift between 1st→2nd third, delta[1] = 2nd→3rd
        turning_point = deltas.index(max_delta) + 2  # 2 or 3

    coaching_note = _coaching_for(arc)

    return SentimentArc(
        scores=scores,
        labels=labels,
        arc=arc,
        momentum=momentum,
        turning_point=turning_point,
        coaching_note=coaching_note,
        segment_texts=segments,
    )
