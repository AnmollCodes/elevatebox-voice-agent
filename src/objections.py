"""
Objection detection and personalised rebuttal generation.

Most sales AI fires the same WhatsApp template regardless of what the lead said.
This module changes that: it reads the transcript, identifies the specific objection
type the lead raised, captures the exact phrase they used, and generates a WhatsApp
message that acknowledges that specific concern by name.

Objection taxonomy (4 types):
  PRICE      — budget concerns, too expensive, can't afford
  TIMING     — not now, too busy, call me later
  TRUST      — who are you, any examples, have you done this before
  COMPETITION — already using someone else, have a website, talked to others

A single call can surface multiple objections. We return all of them, ranked by
confidence, so the follow-up can address the primary one while acknowledging others.

WhatsApp rebuttals are written in a warm, human tone — not corporate-speak.
They open by echoing the lead's own words back to them (the "mirror technique"),
then pivot to a specific proof point or offer. Each rebuttal is under 160 chars
so it reads naturally on a phone screen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ObjectionType(str, Enum):
    PRICE       = "price"
    TIMING      = "timing"
    TRUST       = "trust"
    COMPETITION = "competition"
    NONE        = "none"


# ---------------------------------------------------------------------------
# Signal bank — (pattern, objection_type, weight, mirror_phrase)
# mirror_phrase is used to echo the lead's concern back in the rebuttal
# ---------------------------------------------------------------------------

@dataclass
class _Signal:
    pattern: str
    objection: ObjectionType
    weight: float
    mirror: str  # short phrase that captures what the lead said


_SIGNALS: list[_Signal] = [
    # PRICE objections
    _Signal(r"too expensive|bahut mehnga|mehnga hai|costly",
            ObjectionType.PRICE, 0.9, "it's too expensive"),
    _Signal(r"can'?t afford|budget nahi|budget kam|tight budget|no budget",
            ObjectionType.PRICE, 0.9, "budget is tight right now"),
    _Signal(r"kitna lagega|what('?s| is) (the )?cost|price (kya|batao)|how much",
            ObjectionType.PRICE, 0.6, "you asked about the price"),
    _Signal(r"₹|rs\.?\s*\d|rupees?\s*\d|\d\s*(?:k|lakh|l)\b",
            ObjectionType.PRICE, 0.5, "you mentioned a specific budget"),

    # TIMING objections
    _Signal(r"abhi nahi|not (right )?now|not yet|baad mein|later",
            ObjectionType.TIMING, 0.8, "now isn't the right time"),
    _Signal(r"busy|no time|bahut kaam|occupied|hectic",
            ObjectionType.TIMING, 0.7, "you're busy right now"),
    _Signal(r"next (month|quarter|year)|agle mahine|call me (later|next|back)",
            ObjectionType.TIMING, 0.8, "you'd prefer to talk later"),
    _Signal(r"not (ready|prepared)|sochna hai|thinking about|planning stage",
            ObjectionType.TIMING, 0.6, "you're still planning"),

    # TRUST objections
    _Signal(r"who (are|is) (you|this)|pehle nahi suna|never heard|new company",
            ObjectionType.TRUST, 0.9, "you haven't heard of us before"),
    _Signal(r"any (example|sample|portfolio|work)|show me|koi example|proof",
            ObjectionType.TRUST, 0.8, "you wanted to see examples of our work"),
    _Signal(r"kaise pata|how do i know|can (you )?guarantee|guarantee kya",
            ObjectionType.TRUST, 0.9, "you wanted a guarantee"),
    _Signal(r"review|rating|testimonial|client bolo|reference",
            ObjectionType.TRUST, 0.7, "you asked for client references"),

    # COMPETITION objections
    _Signal(r"already (have|using|got)|pehle se hai|website already|existing site",
            ObjectionType.COMPETITION, 0.9, "you already have a website"),
    _Signal(r"(talking|working|spoke) (to|with) (someone|another|others|dusra)",
            ObjectionType.COMPETITION, 0.8, "you're talking to other vendors"),
    _Signal(r"freelancer|dusra company|another agency|local developer",
            ObjectionType.COMPETITION, 0.8, "you have another option in mind"),
    _Signal(r"shopify|woocommerce|wix|squarespace|dukaan|meesho",
            ObjectionType.COMPETITION, 0.7, "you mentioned another platform"),
]


# ---------------------------------------------------------------------------
# Rebuttal templates — keyed by ObjectionType
# Placeholders: {mirror} = what the lead said, {exact_phrase} = verbatim quote
# ---------------------------------------------------------------------------

_REBUTTALS: dict[ObjectionType, list[str]] = {
    ObjectionType.PRICE: [
        "You mentioned {mirror} — totally fair. We have starter packages from ₹15k and zero upfront. Want me to send the exact breakdown? 👇",
        "Budget is always a valid concern. Since {mirror}, let me share our ₹15k starter plan — no hidden costs. I'll send it right now.",
        "I hear you on the price. Our entry package is ₹15k with full support. Let me send the cost sheet so you have the exact numbers. 📊",
    ],
    ObjectionType.TIMING: [
        "No pressure at all! Since {mirror}, I'll send our one-pager — takes 2 min to read when you're free. Your number: {phone}",
        "Totally understand you're busy. I'll send a quick overview on WhatsApp so you can review at your own pace. 🙏",
        "Got it — {mirror}. I'll ping you the details now. Look for the message from this number. Talk soon! 😊",
    ],
    ObjectionType.TRUST: [
        "Makes complete sense — {mirror}. I'm sending you 3 live stores we built, plus the team's portfolio. Judge for yourself. 👀",
        "Fair question! {mirror}. I'm dropping our client stories and a live demo store in this chat right now. Take a look! ✅",
        "100% valid. {mirror}. Check out the portfolio I'm sending — real stores, real numbers. Then decide. No pressure. 🙌",
    ],
    ObjectionType.COMPETITION: [
        "Noted — {mirror}. Our edge: we build, maintain, AND market your store. One team, one call, zero handoff headaches. Details incoming! 🚀",
        "Understood! Since {mirror}, just know we offer a free audit of any existing site — no strings. Want me to send the audit form?",
        "That's great context. Even if {mirror}, we can upgrade or migrate with zero downtime. Sending a comparison sheet now. 📋",
    ],
    ObjectionType.NONE: [
        "Thanks for the conversation! I'm sending over our portfolio and pricing so you have everything you need. 📩",
    ],
}


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DetectedObjection:
    """A single objection found in the transcript."""
    objection_type: ObjectionType
    exact_phrase: str        # verbatim text that triggered detection
    mirror_phrase: str       # human-friendly echo of what they said
    confidence: float        # 0.0–1.0
    rebuttal: str            # ready-to-send WhatsApp rebuttal


@dataclass
class ObjectionMap:
    """
    All objections detected in a transcript, with the primary one first.

    primary_objection   The highest-confidence objection type.
    objections          All detected objections, ranked by confidence.
    whatsapp_rebuttal   The rebuttal for the primary objection, phone-number-aware.
    no_objection        True when the lead raised no identifiable concern.
    """
    primary_objection: ObjectionType
    objections: list[DetectedObjection]
    whatsapp_rebuttal: str
    no_objection: bool

    def as_dict(self) -> dict:
        return {
            "primary_objection": self.primary_objection.value,
            "no_objection": self.no_objection,
            "objections": [
                {
                    "type": o.objection_type.value,
                    "exact_phrase": o.exact_phrase,
                    "mirror_phrase": o.mirror_phrase,
                    "confidence": round(o.confidence, 3),
                    "rebuttal": o.rebuttal,
                }
                for o in self.objections
            ],
            "whatsapp_rebuttal": self.whatsapp_rebuttal,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_phrase(pattern: str, text: str, window: int = 60) -> str:
    """Return a short context window around the first regex match."""
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return ""
    start = max(0, m.start() - 20)
    end = min(len(text), m.end() + 40)
    snippet = text[start:end].strip()
    # clean up partial words at boundaries
    if start > 0 and not text[start - 1].isspace():
        snippet = "…" + snippet
    if end < len(text) and not text[end].isspace():
        snippet = snippet + "…"
    return snippet[:window]


def _pick_rebuttal(
    objection: ObjectionType,
    mirror: str,
    exact_phrase: str,
    phone: str,
    index: int = 0,
) -> str:
    """Select and render a rebuttal template."""
    templates = _REBUTTALS.get(objection, _REBUTTALS[ObjectionType.NONE])
    template = templates[index % len(templates)]
    return (
        template
        .replace("{mirror}", mirror)
        .replace("{exact_phrase}", exact_phrase[:40] if exact_phrase else mirror)
        .replace("{phone}", phone or "your number")
    )


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------

def detect_objections(
    transcript: str,
    phone: str = "",
    rebuttal_index: int = 0,
) -> ObjectionMap:
    """
    Scan a transcript for sales objections and generate personalised rebuttals.

    Parameters
    ----------
    transcript      Full conversation text.
    phone           Lead's phone number (used in timing rebuttals).
    rebuttal_index  Which template variant to use (0-based); useful for A/B tests.

    Returns
    -------
    ObjectionMap with all detected objections and a ready-to-send WhatsApp rebuttal.
    """
    text = transcript.strip()
    found: dict[ObjectionType, DetectedObjection] = {}

    for sig in _SIGNALS:
        m = re.search(sig.pattern, text, re.IGNORECASE)
        if not m:
            continue
        exact = _extract_phrase(sig.pattern, text)
        obj_type = sig.objection

        if obj_type not in found or sig.weight > found[obj_type].confidence:
            rebuttal = _pick_rebuttal(obj_type, sig.mirror, exact, phone, rebuttal_index)
            found[obj_type] = DetectedObjection(
                objection_type=obj_type,
                exact_phrase=exact,
                mirror_phrase=sig.mirror,
                confidence=sig.weight,
                rebuttal=rebuttal,
            )

    ranked = sorted(found.values(), key=lambda o: o.confidence, reverse=True)

    if not ranked:
        no_obj_rebuttal = _pick_rebuttal(ObjectionType.NONE, "", "", phone, rebuttal_index)
        return ObjectionMap(
            primary_objection=ObjectionType.NONE,
            objections=[],
            whatsapp_rebuttal=no_obj_rebuttal,
            no_objection=True,
        )

    primary = ranked[0]
    return ObjectionMap(
        primary_objection=primary.objection_type,
        objections=ranked,
        whatsapp_rebuttal=primary.rebuttal,
        no_objection=False,
    )
