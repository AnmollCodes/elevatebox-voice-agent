#!/usr/bin/env python3
"""
Demo: Real-time buyer persona detection (no API keys needed).

Detects 4 buyer archetypes from call transcripts:
  - Executive    — wants ROI and credibility, closes fast
  - Explorer     — comparing options, asks many feature questions
  - Budget       — cost is the primary filter
  - Time-Pressured — has a hard deadline (festival season, product launch)

The detected persona's coaching_instruction is what gets injected into
GPT-4o mid-call to adapt the sales pitch in real time.

Run:
    python examples/demo_persona.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.persona import detect_persona, get_followup_angle, BuyerPersona

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
CYN   = "\033[96m"
GRN   = "\033[92m"
YEL   = "\033[93m"
MAG   = "\033[95m"
RED   = "\033[91m"

PERSONA_COLOURS = {
    BuyerPersona.EXECUTIVE:      MAG,
    BuyerPersona.EXPLORER:       CYN,
    BuyerPersona.BUDGET:         YEL,
    BuyerPersona.TIME_PRESSURED: RED,
    BuyerPersona.UNKNOWN:        DIM,
}

PERSONA_EMOJI = {
    BuyerPersona.EXECUTIVE:      "👔",
    BuyerPersona.EXPLORER:       "🔍",
    BuyerPersona.BUDGET:         "💰",
    BuyerPersona.TIME_PRESSURED: "⏰",
    BuyerPersona.UNKNOWN:        "❓",
}

SAMPLES = [
    {
        "label": "Executive — Wants to close fast",
        "transcript": (
            "Who else have you built websites for? Do you have examples? "
            "My team will handle the content. Can we close this today? "
            "We have multiple branches and we're expanding."
        ),
    },
    {
        "label": "Explorer — Researching options",
        "transcript": (
            "Is it better than Shopify? Can it do product variants? "
            "What technology do you use? How does it work exactly? "
            "I'm still exploring and comparing options right now."
        ),
    },
    {
        "label": "Budget-Constrained — Price is everything",
        "transcript": (
            "Tell me the cost first. How much does it cost? "
            "Budget is limited, any discount available? "
            "Can I pay in parts? Freelancer quotes me less."
        ),
    },
    {
        "label": "Time-Pressured — Festival season deadline",
        "transcript": (
            "I need it by Diwali. Can you do it faster? "
            "In how many days can you deliver? As soon as possible please. "
            "Is there a rush option? I want to launch before the festive season."
        ),
    },
    {
        "label": "Hindi — Budget constrained",
        "transcript": (
            "Kitna lagega? Kuch discount milega? Budget kam hai mere paas. "
            "Pehle price batao, phir sochte hain. EMI pe hoga kya?"
        ),
    },
    {
        "label": "Telugu — Time-pressured",
        "transcript": (
            "Twaraga kavali, pandaga mundu ready avvali. "
            "Yenni rojullo chestaru? Ippude start cheyandi please."
        ),
    },
    {
        "label": "Unknown — Not enough signals",
        "transcript": "Hello, yes I'm listening. Ok. Sounds interesting.",
    },
]


def print_result(sample: dict) -> None:
    result = detect_persona(sample["transcript"])
    colour = PERSONA_COLOURS.get(result.persona, DIM)
    emoji  = PERSONA_EMOJI.get(result.persona, "?")
    conf_pct = int(result.confidence * 100)
    label = result.persona.value.replace("_", " ").title()

    print(f"\n  {DIM}{'─' * 58}{RESET}")
    print(f"  {BOLD}{sample['label']}{RESET}")
    print(f"  Transcript: {DIM}{sample['transcript'][:80]}{'...' if len(sample['transcript']) > 80 else ''}{RESET}")
    print()
    print(f"  Persona   : {colour}{BOLD}{emoji} {label}{RESET}   ({conf_pct}% confidence)")

    if result.matched_signals:
        signals_str = ", ".join(result.matched_signals[:4])
        if len(result.matched_signals) > 4:
            signals_str += f" +{len(result.matched_signals) - 4} more"
        print(f"  Signals   : {DIM}{signals_str}{RESET}")

    print(f"\n  {BOLD}Mid-call coaching injection:{RESET}")
    # Word-wrap the coaching instruction at 60 chars
    words = result.coaching_instruction.split()
    lines, line = [], []
    for word in words:
        if sum(len(w) + 1 for w in line) + len(word) > 58:
            lines.append(" ".join(line))
            line = [word]
        else:
            line.append(word)
    if line:
        lines.append(" ".join(line))
    for ln in lines:
        print(f"  {colour}{ln}{RESET}")

    print(f"\n  {BOLD}WhatsApp follow-up angle:{RESET}")
    angle_words = get_followup_angle(result.persona).split()
    lines, line = [], []
    for word in angle_words:
        if sum(len(w) + 1 for w in line) + len(word) > 58:
            lines.append(" ".join(line))
            line = [word]
        else:
            line.append(word)
    if line:
        lines.append(" ".join(line))
    for ln in lines:
        print(f"  {DIM}{ln}{RESET}")


def main() -> None:
    print(f"\n{BOLD}ElevateBox Voice Agent — Persona Detection Demo{RESET}")
    print(f"{DIM}No API keys required. Runs entirely offline.{RESET}")
    print(f"\n{DIM}Detecting buyer personas from {len(SAMPLES)} sample transcripts...{RESET}")

    for sample in SAMPLES:
        print_result(sample)

    print(f"\n  {DIM}{'─' * 58}{RESET}")
    print(f"\n{GRN}✓ Done.{RESET}  In production, detect_persona() is called with the")
    print(f"  partial transcript after each lead utterance. The coaching_instruction")
    print(f"  is returned as the `detect_buyer_persona` function call result to Vapi,")
    print(f"  which passes it to GPT-4o for the next turn.\n")


if __name__ == "__main__":
    main()
