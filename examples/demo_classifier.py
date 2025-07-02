#!/usr/bin/env python3
"""
Demo: Evidence-based intent classifier (no API keys needed).

This demo runs the full classification pipeline locally — no Vapi, no OpenAI,
no Twilio, no internet connection required. It shows how the system classifies
real-world sales call transcripts across English, Hindi, and Telugu.

Run:
    python examples/demo_classifier.py

What it shows:
  - HOT / WARM / COLD classification with confidence scores
  - Named evidence trail (which exact phrases drove the decision)
  - Budget extraction from transcript
  - Language detection
  - Reasoning summary used in WhatsApp follow-up
"""

import sys
import os

# Allow running from project root without installing the package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.intelligence import classify_with_evidence

# ─── Sample transcripts ──────────────────────────────────────────────────────
# These represent realistic call snippets across languages and intent levels.

SAMPLE_CALLS = [
    {
        "id": "EN-HOT-01",
        "label": "English — Strong buying intent",
        "transcript": """
        Agent: Hi, this is Priya from ElevateBox. We help small businesses
               get online with a professional e-commerce website.
        Lead:  Oh nice. How much does it cost?
        Agent: Our packages start at ₹15,000 for a complete online store.
        Lead:  That sounds reasonable. When can you start? I need it live
               before the festival season. Send me the details on WhatsApp.
        Agent: Absolutely! Can I ask — what products do you sell?
        Lead:  Handmade jewellery. I sell around 200 pieces a month offline.
               Let's move forward, I'm ready to proceed.
        """,
    },
    {
        "id": "HI-WARM-01",
        "label": "Hindi — Interested but budget blocked",
        "transcript": """
        Agent: Namaste ji, main Priya bol rahi hoon ElevateBox se.
        Lead:  Haan bolo.
        Agent: Aapka online store banana chahte hain kya?
        Lead:  Dekhte hain. Budget nahi hai abhi. Sochna padega thoda.
               Partner se poochna padega pehle. Kal baat karte hain?
        Agent: Bilkul ji. Kab convenient rahega?
        Lead:  Kal subah 10 baje theek rahega.
        """,
    },
    {
        "id": "TE-COLD-01",
        "label": "Telugu — Not interested",
        "transcript": """
        Agent: Namaskaram, nenu Priya ni, ElevateBox nundi matladutunna.
        Lead:  Cheppandi.
        Agent: Mee business ki website cheyalani anipistundaa?
        Lead:  Avasaram ledu. Maa dukaanam chinna di, online ki raaledhu.
               Matladataniki time ledu, chuddam anipinchindi. Sarele.
        """,
    },
    {
        "id": "EN-WARM-02",
        "label": "English — Decision maker absent",
        "transcript": """
        Agent: How does the pricing work for a starter store?
        Lead:  It's interesting, but my brother handles all the finances.
               I'll ask my brother and let you know. Maybe next month
               we can decide. I'm considering it, just not right now.
        """,
    },
    {
        "id": "EN-HOT-02",
        "label": "English — With budget mention",
        "transcript": """
        Agent: What budget are you thinking for the website?
        Lead:  I have about ₹50,000 to spend. How do I pay once we're done?
               Where do I pay? Can we begin next week?
        """,
    },
    {
        "id": "MIXED-01",
        "label": "Code-switching (Hindi + English)",
        "transcript": """
        Lead:  Yaar, does it support payment gateway? Kitna lagega total?
               Mujhe 2 weeks mein chahiye. Kab shuru kar sakte ho?
               I sell handmade products online already. Thoda discount milega?
        """,
    },
]

# ─── Colours for terminal output ─────────────────────────────────────────────

RESET = "\033[0m"
BOLD  = "\033[1m"
RED   = "\033[91m"
YEL   = "\033[93m"
BLU   = "\033[94m"
GRN   = "\033[92m"
DIM   = "\033[2m"

STATUS_COLOURS = {
    "HOT":  RED,
    "WARM": YEL,
    "COLD": BLU,
}

BAR_CHARS = "█"


def confidence_bar(score: float, width: int = 20) -> str:
    filled = int(score * width)
    return BAR_CHARS * filled + "░" * (width - filled)


def print_result(call: dict) -> None:
    result = classify_with_evidence(call["transcript"])
    status = result.status.value.upper()
    colour = STATUS_COLOURS.get(status, "")
    conf_pct = int(result.confidence * 100)

    print(f"\n{'─' * 62}")
    print(f"{BOLD}{call['id']}{RESET}  {DIM}{call['label']}{RESET}")
    print(f"{'─' * 62}")

    # Status badge + confidence bar
    print(f"  Status   : {colour}{BOLD}{status:4}{RESET}   "
          f"{confidence_bar(result.confidence)} {conf_pct}%")

    # Language + budget
    print(f"  Language : {result.language_detected}")
    if result.budget_detected:
        print(f"  Budget   : {GRN}{result.budget_detected}{RESET}")

    # Evidence trail
    top = result.top_evidence(3)
    if top:
        print(f"  Evidence :")
        for e in top:
            method_tag = f"{DIM}[{e.method}]{RESET}"
            print(f"    • {e.signal_name:<30} "
                  f"weight={e.weight:.2f}  {method_tag}")
            print(f"      matched: \"{e.matched_text}\"")

    # Reasoning summary (what would go in the WhatsApp message)
    print(f"\n  {DIM}WhatsApp reasoning:{RESET}")
    print(f"  {result.reasoning_summary}")


def main() -> None:
    print(f"\n{BOLD}ElevateBox Voice Agent — Classification Demo{RESET}")
    print(f"{DIM}No API keys required. Runs entirely offline.{RESET}")
    print(f"\n{DIM}Testing {len(SAMPLE_CALLS)} sample call transcripts...{RESET}")

    for call in SAMPLE_CALLS:
        print_result(call)

    print(f"\n{'─' * 62}")
    print(f"\n{GRN}✓ Done.{RESET} To classify your own transcript, import and call:")
    print(f"  {DIM}from src.intelligence import classify_with_evidence{RESET}")
    print(f"  {DIM}result = classify_with_evidence(your_transcript_string){RESET}")
    print()


if __name__ == "__main__":
    main()
