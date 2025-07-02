#!/usr/bin/env python3
"""
Demo: Per-call SVG architecture diagram generator (no API keys needed).

Generates a call-specific SVG diagram and saves it to examples/output/.
Open the file in any browser to see it rendered.

Each call gets its own diagram showing:
  - The call pipeline (Phone → Vapi → GPT-4o → WhatsApp)
  - Lead classification badge (HOT / WARM / COLD with colour)
  - Detected buyer persona
  - Top classification signal phrases
  - Budget mention
  - Detected language
  - Candidate attribution footer

In production, the diagram is served at /diagram/{call_id}.svg and
its URL is included in the post-call WhatsApp message as a media attachment.

Run:
    python examples/demo_diagram.py

Output: examples/output/diagram_hot.svg, diagram_warm.svg, diagram_cold.svg
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.classifier import LeadStatus
from src.diagram import generate_call_diagram

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

SAMPLE_CALLS = [
    {
        "filename": "diagram_hot.svg",
        "call_id": "demo-hot-abc123",
        "lead_status": LeadStatus.HOT,
        "persona": "executive",
        "top_signals": [
            "send me the details",
            "how do i pay",
            "when can you start",
        ],
        "budget": "₹50,000",
        "language": "English",
    },
    {
        "filename": "diagram_warm.svg",
        "call_id": "demo-warm-def456",
        "lead_status": LeadStatus.WARM,
        "persona": "budget_constrained",
        "top_signals": [
            "budget is tight",
            "need to discuss",
        ],
        "budget": None,
        "language": "Hindi",
    },
    {
        "filename": "diagram_cold.svg",
        "call_id": "demo-cold-ghi789",
        "lead_status": LeadStatus.COLD,
        "persona": "unknown",
        "top_signals": [
            "not interested",
            "already have a website",
        ],
        "budget": None,
        "language": "Telugu",
    },
]

RESET = "\033[0m"
BOLD  = "\033[1m"
DIM   = "\033[2m"
GRN   = "\033[92m"
RED   = "\033[91m"
YEL   = "\033[93m"

STATUS_COLOURS = {"HOT": RED, "WARM": YEL, "COLD": "\033[94m"}


def main() -> None:
    print(f"\n{BOLD}ElevateBox Voice Agent — Diagram Generator Demo{RESET}")
    print(f"{DIM}No API keys required. Generates SVG files locally.{RESET}\n")

    for call in SAMPLE_CALLS:
        svg_bytes = generate_call_diagram(
            call_id=call["call_id"],
            lead_status=call["lead_status"],
            persona=call["persona"],
            top_signals=call["top_signals"],
            budget=call["budget"],
            language=call["language"],
            candidate_name="Your Name Here",
        )

        out_path = os.path.join(OUTPUT_DIR, call["filename"])
        with open(out_path, "wb") as f:
            f.write(svg_bytes)

        status = call["lead_status"].value.upper()
        colour = STATUS_COLOURS.get(status, "")
        size_kb = len(svg_bytes) / 1024

        print(f"  {GRN}✓{RESET} {colour}{BOLD}{status}{RESET}  →  {call['filename']}  "
              f"{DIM}({size_kb:.1f} KB){RESET}")

    print(f"\n  Saved to: {OUTPUT_DIR}/")
    print(f"\n  Open any .svg file in your browser to preview it.")
    print(f"  In production, each diagram is served at:")
    print(f"  {DIM}https://your-app.com/diagram/{{call_id}}.svg{RESET}")
    print(f"  and that URL is included as the WhatsApp media attachment.\n")


if __name__ == "__main__":
    main()
