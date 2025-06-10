"""
Per-call SVG architecture diagram generator.

Why this exists
---------------
Every candidate sends a static architecture diagram in their WhatsApp follow-up.
This module generates a diagram that is specific to the call that just happened:
the lead's detected persona, classification status, and key signal phrases are
embedded in the diagram. The caller receives a visual that says "this was built
for your conversation" — not a template screenshot.

Engineering trade-off
---------------------
We generate SVG, not PNG. Reasons:
  - Zero external dependencies (no Pillow, no Cairo, no Playwright)
  - SVG is text — it can be generated in < 1ms, no file I/O required
  - FastAPI serves it directly from memory at /diagram/{call_id}.svg
  - Twilio WhatsApp renders images from URLs, so the SVG is fetched and
    displayed when the recipient opens the message

The diagram is cached in memory by call_id so repeated GETs return the
same bytes without regeneration.
"""

from __future__ import annotations

import hashlib
import html
import textwrap
from typing import Optional

from .classifier import LeadStatus


# ---------------------------------------------------------------------------
# In-memory cache: call_id → SVG bytes
# Production would use Redis with a 24h TTL; here a plain dict is fine
# since diagrams are small (~4KB each) and calls are short-lived.
# ---------------------------------------------------------------------------

_cache: dict[str, bytes] = {}


# ---------------------------------------------------------------------------
# Status colour palette
# ---------------------------------------------------------------------------

_STATUS_COLOURS = {
    LeadStatus.HOT:  ("#FF4444", "#FFE0E0", "🔥"),
    LeadStatus.WARM: ("#FF8C00", "#FFF3E0", "🌡️"),
    LeadStatus.COLD: ("#4488FF", "#E0EAFF", "❄️"),
}

_DEFAULT_COLOUR = ("#888888", "#F5F5F5", "?")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_call_diagram(
    call_id: str,
    lead_status: LeadStatus,
    persona: Optional[str] = None,
    top_signals: Optional[list[str]] = None,
    budget: Optional[str] = None,
    language: Optional[str] = None,
    candidate_name: str = "Anmol Agarwal",
) -> bytes:
    """
    Generate (or return cached) an SVG architecture diagram for a call.

    The diagram shows:
      - The call pipeline (Phone → Vapi → GPT-4o → WhatsApp)
      - Call outcome badge (HOT / WARM / COLD with colour)
      - Detected persona (if available)
      - Top signal phrases that drove the classification
      - Budget mention (if detected)
      - Detected language
      - Candidate attribution footer

    Args:
        call_id:        Vapi call ID — used as cache key and for display
        lead_status:    Classification outcome
        persona:        Detected buyer persona string (optional)
        top_signals:    List of up to 3 signal phrase strings (optional)
        budget:         Budget string extracted from transcript (optional)
        language:       "English", "Hindi", or "Telugu" (optional)
        candidate_name: Name shown in attribution footer

    Returns:
        UTF-8 encoded SVG bytes ready to serve as image/svg+xml.
    """
    if call_id in _cache:
        return _cache[call_id]

    svg = _render(
        call_id=call_id,
        lead_status=lead_status,
        persona=persona,
        top_signals=top_signals or [],
        budget=budget,
        language=language or "English",
        candidate_name=candidate_name,
    )
    encoded = svg.encode("utf-8")
    _cache[call_id] = encoded
    return encoded


def get_cached_diagram(call_id: str) -> Optional[bytes]:
    """Return cached SVG bytes, or None if not generated yet."""
    return _cache.get(call_id)


def clear_cache(call_id: str) -> None:
    """Remove a specific diagram from cache (e.g., after call cleanup)."""
    _cache.pop(call_id, None)


def cache_size() -> int:
    """Number of diagrams currently cached."""
    return len(_cache)


# ---------------------------------------------------------------------------
# SVG renderer
# ---------------------------------------------------------------------------

_W = 720   # canvas width
_H = 580   # canvas height


def _render(
    call_id: str,
    lead_status: LeadStatus,
    persona: Optional[str],
    top_signals: list[str],
    budget: Optional[str],
    language: str,
    candidate_name: str,
) -> str:
    colour, bg, emoji = _STATUS_COLOURS.get(lead_status, _DEFAULT_COLOUR)

    # Shorten call_id for display
    short_id = call_id[:8] + "…" if len(call_id) > 8 else call_id

    # Build signal rows
    signal_rows = ""
    for i, sig in enumerate(top_signals[:3]):
        safe = html.escape(sig[:52] + ("…" if len(sig) > 52 else ""))
        y = 382 + i * 22
        signal_rows += f'<text x="400" y="{y}" class="signal">› {safe}</text>\n'

    if not signal_rows:
        signal_rows = '<text x="400" y="382" class="signal">› No strong signals detected</text>\n'

    persona_text = html.escape(persona.replace("_", " ").title() if persona else "Unknown")
    budget_text = html.escape(budget) if budget else "Not mentioned"
    status_text = lead_status.value.upper()
    lang_short = language[:2].upper()

    return f"""<?xml version="1.0" encoding="utf-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" viewBox="0 0 {_W} {_H}">
  <defs>
    <style>
      .title   {{ font: bold 18px 'Segoe UI', sans-serif; fill: #1a1a2e; }}
      .sub     {{ font: 13px 'Segoe UI', sans-serif; fill: #555; }}
      .label   {{ font: bold 12px 'Segoe UI', sans-serif; fill: #333; }}
      .small   {{ font: 11px 'Segoe UI', sans-serif; fill: #666; }}
      .badge   {{ font: bold 20px 'Segoe UI', sans-serif; fill: {colour}; }}
      .node    {{ font: bold 12px 'Segoe UI', sans-serif; fill: #fff; }}
      .signal  {{ font: 11px 'Courier New', monospace; fill: #444; }}
      .footer  {{ font: 10px 'Segoe UI', sans-serif; fill: #aaa; }}
    </style>
    <linearGradient id="hdr" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#1a1a2e"/>
      <stop offset="100%" stop-color="#16213e"/>
    </linearGradient>
    <filter id="shadow">
      <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.15"/>
    </filter>
  </defs>

  <!-- Background -->
  <rect width="{_W}" height="{_H}" rx="12" fill="#f8f9fc"/>

  <!-- Header bar -->
  <rect width="{_W}" height="64" rx="12" fill="url(#hdr)"/>
  <rect y="52" width="{_W}" height="12" fill="#16213e"/>
  <text x="24" y="28" class="title" fill="#fff">ElevateBox Voice Agent</text>
  <text x="24" y="48" class="sub" fill="#8899bb">AI-powered outbound sales · Call {short_id}</text>

  <!-- ── CALL PIPELINE (left column) ─────────────────────────── -->
  <text x="40" y="98" class="label">Call Pipeline</text>

  <!-- Phone node -->
  <rect x="40" y="108" width="120" height="38" rx="8" fill="#0f3460" filter="url(#shadow)"/>
  <text x="100" y="132" class="node" text-anchor="middle">📞 Lead Phone</text>

  <!-- Arrow -->
  <line x1="100" y1="146" x2="100" y2="172" stroke="#0f3460" stroke-width="2" marker-end="url(#arr)"/>

  <!-- Vapi node -->
  <rect x="40" y="172" width="120" height="38" rx="8" fill="#533483" filter="url(#shadow)"/>
  <text x="100" y="196" class="node" text-anchor="middle">🎙 Vapi STT/TTS</text>

  <!-- Arrow -->
  <line x1="100" y1="210" x2="100" y2="236" stroke="#533483" stroke-width="2" marker-end="url(#arr)"/>

  <!-- GPT-4o node -->
  <rect x="40" y="236" width="120" height="38" rx="8" fill="#10a37f" filter="url(#shadow)"/>
  <text x="100" y="255" class="node" text-anchor="middle">🧠 GPT-4o</text>
  <text x="100" y="268" class="node" text-anchor="middle" style="font-size:10px">Priya · Sales Agent</text>

  <!-- Arrow -->
  <line x1="100" y1="274" x2="100" y2="300" stroke="#10a37f" stroke-width="2" marker-end="url(#arr)"/>

  <!-- Classifier node -->
  <rect x="40" y="300" width="120" height="38" rx="8" fill="#e94560" filter="url(#shadow)"/>
  <text x="100" y="319" class="node" text-anchor="middle">⚡ Classifier</text>
  <text x="100" y="332" class="node" text-anchor="middle" style="font-size:10px">Evidence Engine</text>

  <!-- Arrow -->
  <line x1="100" y1="338" x2="100" y2="364" stroke="#e94560" stroke-width="2" marker-end="url(#arr)"/>

  <!-- WhatsApp node -->
  <rect x="40" y="364" width="120" height="38" rx="8" fill="#25d366" filter="url(#shadow)"/>
  <text x="100" y="383" class="node" text-anchor="middle">💬 WhatsApp</text>
  <text x="100" y="396" class="node" text-anchor="middle" style="font-size:10px">Twilio · Mid-call</text>

  <!-- Arrow marker -->
  <defs>
    <marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
      <path d="M0,0 L0,6 L8,3 z" fill="#999"/>
    </marker>
  </defs>

  <!-- Vertical separator -->
  <line x1="200" y1="80" x2="200" y2="{_H - 40}" stroke="#e0e0e0" stroke-width="1" stroke-dasharray="4,4"/>

  <!-- ── CALL OUTCOME (right column) ────────────────────────── -->
  <text x="220" y="98" class="label">Call Outcome</text>

  <!-- Status badge -->
  <rect x="220" y="108" width="{_W - 240}" height="68" rx="10" fill="{bg}" stroke="{colour}" stroke-width="2"/>
  <text x="{_W // 2 + 30}" y="136" class="badge" text-anchor="middle">{emoji} {status_text}</text>
  <text x="{_W // 2 + 30}" y="160" class="small" text-anchor="middle" fill="{colour}">Lead Classification · {language}</text>

  <!-- Meta grid -->
  <rect x="220" y="188" width="230" height="96" rx="8" fill="#fff" filter="url(#shadow)"/>
  <text x="235" y="208" class="label">Persona Detected</text>
  <text x="235" y="226" class="small">{persona_text}</text>
  <line x1="235" y1="233" x2="438" y2="233" stroke="#eee" stroke-width="1"/>
  <text x="235" y="250" class="label">Budget Signal</text>
  <text x="235" y="268" class="small">{budget_text}</text>
  <line x1="235" y1="275" x2="438" y2="275" stroke="#eee" stroke-width="1"/>

  <!-- Right meta -->
  <rect x="464" y="188" width="{_W - 484}" height="96" rx="8" fill="#fff" filter="url(#shadow)"/>
  <text x="479" y="208" class="label">Language</text>
  <text x="479" y="226" class="small">{language}</text>
  <line x1="479" y1="233" x2="{_W - 20}" y2="233" stroke="#eee" stroke-width="1"/>
  <text x="479" y="250" class="label">Call ID</text>
  <text x="479" y="268" class="small" style="font-size:9px">{html.escape(call_id[:20])}</text>

  <!-- Evidence signals panel -->
  <rect x="220" y="298" width="{_W - 240}" height="108" rx="8" fill="#fff" filter="url(#shadow)"/>
  <text x="235" y="320" class="label">Top Classification Signals</text>
  <line x1="235" y1="328" x2="{_W - 24}" y2="328" stroke="#eee" stroke-width="1"/>
  {signal_rows}

  <!-- ── TECH STACK PILLS ──────────────────────────────────── -->
  <text x="220" y="428" class="label">Stack</text>
  {''.join(_pill(i, tech) for i, tech in enumerate(["FastAPI", "Vapi.ai", "GPT-4o", "Twilio", "APScheduler"]))}

  <!-- Footer -->
  <rect y="{_H - 36}" width="{_W}" height="36" rx="0" fill="#1a1a2e"/>
  <rect y="{_H - 36}" width="{_W}" height="4" fill="#0f3460"/>
  <text x="24" y="{_H - 14}" class="footer" fill="#8899bb">Built by {html.escape(candidate_name)} · ElevateBox SDE Intern Assignment · ElevateScale Technologies</text>
  <text x="{_W - 24}" y="{_H - 14}" class="footer" fill="#8899bb" text-anchor="end">elevatebox-voice-agent</text>
</svg>"""


def _pill(index: int, text: str) -> str:
    """Render a small technology tag pill."""
    colours = ["#0f3460", "#533483", "#10a37f", "#e94560", "#f5a623"]
    x = 220 + index * 96
    c = colours[index % len(colours)]
    return (
        f'<rect x="{x}" y="434" width="88" height="24" rx="12" fill="{c}"/>'
        f'<text x="{x + 44}" y="450" class="small" text-anchor="middle" fill="#fff">{html.escape(text)}</text>'
    )
