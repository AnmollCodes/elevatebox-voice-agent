#!/usr/bin/env python3
"""
Demo: Full post-call pipeline simulation (no API keys needed).

Simulates what happens after a call ends:
  1. Classify the transcript with the evidence engine
  2. Detect the buyer persona
  3. Generate the per-call SVG diagram
  4. Build the WhatsApp message body (without sending it)

This is the complete offline demonstration of the system's intelligence layer.
The only thing missing from the real pipeline is:
  - The live Vapi call (replaced here by a hardcoded transcript)
  - The actual Twilio WhatsApp send (replaced here by printing the message)

Run:
    python examples/demo_full_pipeline.py

For the live call demo (requires API keys), see README.md → Quick Start.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.intelligence import classify_with_evidence
from src.persona import detect_persona, get_followup_angle
from src.diagram import generate_call_diagram
from src.classifier import LeadStatus

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
GRN   = "\033[92m"
YEL   = "\033[93m"
RED   = "\033[91m"
BLU   = "\033[94m"
CYN   = "\033[96m"
MAG   = "\033[95m"

# ─── Example transcript ───────────────────────────────────────────────────────
# A realistic code-switching (Hindi + English) call transcript.
# Replace this with any real transcript to test.

TRANSCRIPT = """
Agent:  Namaste ji! Main Priya bol rahi hoon, ElevateBox se.
        Aapka online store banana chahte hain?

Lead:   Haan haan, actually main soch raha tha iske baare mein.
        Online selling start karna hai. Kitna lagega?

Agent:  Humara basic package ₹15,000 se start hota hai — complete online
        store with payment gateway. 2 weeks mein ready.

Lead:   ₹15,000? Theek hai. Does it support multiple payment options?
        I need UPI, credit card, everything.

Agent:  Ji bilkul, sab kuch include hai. Kya aap mujhe bata sakte hain
        aap kya sell karte hain?

Lead:   Main handmade candles bechta hoon. Festival season aa raha hai —
        Diwali se pehle chahiye definitely.

Agent:  Perfect! Hum express track pe 10 din mein deliver kar sakte hain.
        Aapka budget kya hai overall?

Lead:   Around ₹25,000 rakh sakta hoon. Details bhejo WhatsApp pe.
        Kab shuru kar sakte ho? Let's move forward!
"""

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def section(title: str) -> None:
    print(f"\n{BOLD}{CYN}{'─' * 60}{RESET}")
    print(f"{BOLD}{CYN}  {title}{RESET}")
    print(f"{BOLD}{CYN}{'─' * 60}{RESET}")


def main() -> None:
    print(f"\n{BOLD}ElevateBox Voice Agent — Full Pipeline Demo{RESET}")
    print(f"{DIM}Simulating post-call processing with no API keys.{RESET}")

    # ── Show the transcript ───────────────────────────────────────────────────
    section("1. Call Transcript")
    for line in TRANSCRIPT.strip().splitlines():
        stripped = line.strip()
        if stripped.startswith("Agent:"):
            print(f"  {DIM}{stripped}{RESET}")
        elif stripped.startswith("Lead:"):
            print(f"  {BOLD}{stripped}{RESET}")
        elif stripped:
            print(f"  {DIM}{stripped}{RESET}")

    # ── Step 1: Evidence-based classification ─────────────────────────────────
    section("2. Evidence-Based Classification (intelligence.py)")
    clf = classify_with_evidence(TRANSCRIPT)

    STATUS_COLOURS = {"HOT": RED, "WARM": YEL, "COLD": BLU}
    colour = STATUS_COLOURS.get(clf.status.value.upper(), "")
    conf_pct = int(clf.confidence * 100)

    print(f"\n  Status     : {colour}{BOLD}{clf.status.value.upper()}{RESET}")
    print(f"  Confidence : {conf_pct}%  {'█' * int(clf.confidence * 20)}{'░' * (20 - int(clf.confidence * 20))}")
    print(f"  Language   : {clf.language_detected}")
    if clf.budget_detected:
        print(f"  Budget     : {GRN}{clf.budget_detected}{RESET}")

    print(f"\n  Evidence trail (top signals):")
    for e in clf.top_evidence(4):
        print(f"    {GRN}•{RESET} [{e.method}] \"{e.matched_text}\"")
        print(f"       Signal: {e.signal_name} | Weight: {e.weight:.2f}")

    print(f"\n  Reasoning summary (used in WhatsApp):")
    print(f"  {DIM}{clf.reasoning_summary}{RESET}")

    print(f"\n  readable_evidence() → \"{clf.readable_evidence()}\"")

    # ── Step 2: Persona detection ─────────────────────────────────────────────
    section("3. Buyer Persona Detection (persona.py)")
    persona = detect_persona(TRANSCRIPT)

    PERSONA_COLOURS = {
        "executive": MAG, "explorer": CYN,
        "budget_constrained": YEL, "time_pressured": RED, "unknown": DIM,
    }
    PERSONA_EMOJI = {
        "executive": "👔", "explorer": "🔍",
        "budget_constrained": "💰", "time_pressured": "⏰", "unknown": "❓",
    }
    pcol = PERSONA_COLOURS.get(persona.persona.value, DIM)
    emoji = PERSONA_EMOJI.get(persona.persona.value, "?")
    plabel = persona.persona.value.replace("_", " ").title()

    print(f"\n  Persona    : {pcol}{BOLD}{emoji} {plabel}{RESET}  ({int(persona.confidence * 100)}% confidence)")
    if persona.matched_signals:
        print(f"  Signals    : {DIM}{', '.join(persona.matched_signals[:5])}{RESET}")

    print(f"\n  Mid-call coaching instruction injected into GPT-4o:")
    words = persona.coaching_instruction.split()
    line_buf = []
    for word in words:
        if sum(len(w) + 1 for w in line_buf) + len(word) > 56:
            print(f"  {pcol}{' '.join(line_buf)}{RESET}")
            line_buf = [word]
        else:
            line_buf.append(word)
    if line_buf:
        print(f"  {pcol}{' '.join(line_buf)}{RESET}")

    # ── Step 3: Diagram generation ────────────────────────────────────────────
    section("4. Per-Call SVG Diagram (diagram.py)")
    call_id = "demo-pipeline-001"
    svg_bytes = generate_call_diagram(
        call_id=call_id,
        lead_status=clf.status,
        persona=persona.persona.value,
        top_signals=[e.matched_text for e in clf.top_evidence(3)],
        budget=clf.budget_detected,
        language=clf.language_detected,
        candidate_name="Your Name Here",
    )

    svg_path = os.path.join(OUTPUT_DIR, f"diagram_{call_id}.svg")
    with open(svg_path, "wb") as f:
        f.write(svg_bytes)

    print(f"\n  {GRN}✓{RESET} Generated {len(svg_bytes) / 1024:.1f} KB SVG → {svg_path}")
    print(f"  In production: served at /diagram/{call_id}.svg")
    print(f"  Included as media URL in WhatsApp message.")

    # ── Step 4: WhatsApp message preview ──────────────────────────────────────
    section("5. WhatsApp Message Preview")
    followup_angle = get_followup_angle(persona.persona)

    status_label = clf.status.value.upper()
    budget_line = f"\n💰 Budget mentioned: {clf.budget_detected}" if clf.budget_detected else ""

    if clf.status == LeadStatus.HOT:
        whatsapp_body = f"""🔥 *Great speaking with you!*

You asked about our e-commerce development services and showed strong interest — specifically, you mentioned:
_{clf.readable_evidence()}_

{followup_angle}{budget_line}

📋 *What's included:*
• Complete online store with payment gateway
• Mobile-optimised design
• WhatsApp integration
• 12 months support
• Goes live in 2–14 days

📎 Resume: https://your-resume-url.com/resume.pdf
🏗️ Architecture: https://your-app.com/diagram/{call_id}.svg

📱 +91XXXXXXXXXX  |  ElevateBox by Your Name"""

    elif clf.status == LeadStatus.WARM:
        whatsapp_body = f"""😊 *Thanks for the chat!*

I understand the timing isn't quite right. When you're ready, I'll be here.

{followup_angle}{budget_line}

Our packages start at ₹15,000 — happy to discuss what works for your budget.

📱 +91XXXXXXXXXX  |  ElevateBox by Your Name"""

    else:
        whatsapp_body = f"""👋 *Thanks for picking up!*

No pressure at all — if you ever want to explore getting online, we're here.

Our basic store starts at ₹15,000 and goes live in 2 weeks.

📱 +91XXXXXXXXXX  |  ElevateBox by Your Name"""

    print(f"\n  {DIM}[Would be sent via Twilio to the lead's WhatsApp number]{RESET}\n")
    print(f"  {'─' * 50}")
    for line in whatsapp_body.strip().splitlines():
        print(f"  {line}")
    print(f"  {'─' * 50}")

    # ── Summary ───────────────────────────────────────────────────────────────
    section("Summary")
    print(f"""
  In a live call, this entire post-call pipeline runs in < 200ms
  after the call ends. The WhatsApp fires mid-call (on HOT detection)
  via asyncio.create_task(), so the lead receives it while still
  talking to the agent.

  Classification : {colour}{BOLD}{clf.status.value.upper()}{RESET} ({conf_pct}% confidence)
  Persona        : {pcol}{BOLD}{emoji} {plabel}{RESET}
  Budget         : {GRN}{clf.budget_detected or 'not detected'}{RESET}
  Language       : {clf.language_detected}
  SVG diagram    : {svg_path}
""")


if __name__ == "__main__":
    main()
