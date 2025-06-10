"""
Tests for src/diagram.py — per-call SVG architecture diagram generator.

Validates:
  - SVG output is valid, non-empty bytes
  - Call outcome (HOT/WARM/COLD) is reflected in the diagram
  - Persona, language, budget, signals appear in diagram
  - Caching works: same call_id returns identical bytes
  - Cache management: clear_cache, cache_size, get_cached_diagram
  - HTML entities are escaped (no XSS via signal phrases)
"""

import pytest

from src.classifier import LeadStatus
from src.diagram import (
    cache_size,
    clear_cache,
    generate_call_diagram,
    get_cached_diagram,
)


CALL_ID = "test-call-abc123"
CALL_ID_HOT = "call-hot-001"
CALL_ID_WARM = "call-warm-002"
CALL_ID_COLD = "call-cold-003"


def _make(call_id: str, status: LeadStatus = LeadStatus.HOT, **kwargs) -> bytes:
    return generate_call_diagram(call_id=call_id, lead_status=status, **kwargs)


# ---------------------------------------------------------------------------
# Basic output validation
# ---------------------------------------------------------------------------

class TestSvgOutput:
    def test_returns_bytes(self):
        svg = _make(CALL_ID + "bytes")
        assert isinstance(svg, bytes)

    def test_non_empty(self):
        svg = _make(CALL_ID + "nonempty")
        assert len(svg) > 500

    def test_valid_svg_header(self):
        svg = _make(CALL_ID + "header").decode("utf-8")
        assert svg.startswith("<?xml")
        assert "<svg" in svg

    def test_closes_svg_tag(self):
        svg = _make(CALL_ID + "close").decode("utf-8")
        assert "</svg>" in svg

    def test_utf8_encoded(self):
        svg_bytes = _make(CALL_ID + "utf8")
        decoded = svg_bytes.decode("utf-8")  # must not raise
        assert len(decoded) > 0


# ---------------------------------------------------------------------------
# Call status reflected in diagram
# ---------------------------------------------------------------------------

class TestStatusReflected:
    def test_hot_badge_in_diagram(self):
        svg = _make(CALL_ID_HOT, LeadStatus.HOT).decode("utf-8")
        assert "HOT" in svg

    def test_warm_badge_in_diagram(self):
        svg = _make(CALL_ID_WARM, LeadStatus.WARM).decode("utf-8")
        assert "WARM" in svg

    def test_cold_badge_in_diagram(self):
        svg = _make(CALL_ID_COLD, LeadStatus.COLD).decode("utf-8")
        assert "COLD" in svg

    def test_hot_uses_red_colour(self):
        svg = _make("hot-colour", LeadStatus.HOT).decode("utf-8")
        assert "#FF4444" in svg or "FF4444" in svg.upper()

    def test_cold_uses_blue_colour(self):
        svg = _make("cold-colour", LeadStatus.COLD).decode("utf-8")
        assert "#4488FF" in svg or "4488FF" in svg.upper()


# ---------------------------------------------------------------------------
# Optional metadata in diagram
# ---------------------------------------------------------------------------

class TestMetadataInDiagram:
    def test_persona_appears_in_diagram(self):
        svg = _make(
            "persona-test", LeadStatus.HOT,
            persona="executive",
        ).decode("utf-8")
        assert "Executive" in svg

    def test_budget_appears_in_diagram(self):
        svg = _make(
            "budget-test", LeadStatus.HOT,
            budget="₹50,000",
        ).decode("utf-8")
        assert "50,000" in svg

    def test_language_appears_in_diagram(self):
        svg = _make(
            "lang-test", LeadStatus.WARM,
            language="Telugu",
        ).decode("utf-8")
        assert "Telugu" in svg

    def test_signal_phrase_appears_in_diagram(self):
        svg = _make(
            "signal-test", LeadStatus.HOT,
            top_signals=["Asked to start", "Payment inquiry"],
        ).decode("utf-8")
        assert "Asked to start" in svg

    def test_candidate_name_appears_in_footer(self):
        svg = _make(
            "name-test", LeadStatus.WARM,
            candidate_name="Anmol Agarwal",
        ).decode("utf-8")
        assert "Anmol Agarwal" in svg

    def test_call_id_short_form_in_diagram(self):
        svg = _make("abcdefgh1234", LeadStatus.COLD).decode("utf-8")
        assert "abcdefgh" in svg

    def test_no_budget_shows_not_mentioned(self):
        svg = _make("no-budget-test", LeadStatus.WARM, budget=None).decode("utf-8")
        assert "Not mentioned" in svg


# ---------------------------------------------------------------------------
# XSS / injection protection
# ---------------------------------------------------------------------------

class TestEscaping:
    def test_signal_phrase_is_html_escaped(self):
        svg = _make(
            "xss-test", LeadStatus.HOT,
            top_signals=["<script>alert('xss')</script>"],
        ).decode("utf-8")
        assert "<script>" not in svg
        assert "&lt;script&gt;" in svg or "script" not in svg

    def test_budget_string_is_escaped(self):
        svg = _make(
            "xss-budget", LeadStatus.HOT,
            budget="<img src=x>",
        ).decode("utf-8")
        assert "<img" not in svg

    def test_candidate_name_is_escaped(self):
        svg = _make(
            "xss-name", LeadStatus.WARM,
            candidate_name='"><svg onload="alert(1)',
        ).decode("utf-8")
        # The raw unescaped attribute must not appear; HTML-encoded form is safe
        assert 'onload="' not in svg


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

class TestCaching:
    def setup_method(self):
        """Clear cache entries created by this test class between runs."""
        for cid in ["cache-a", "cache-b", "cache-c"]:
            clear_cache(cid)

    def test_cached_on_first_call(self):
        _make("cache-a", LeadStatus.HOT)
        assert get_cached_diagram("cache-a") is not None

    def test_same_call_id_returns_identical_bytes(self):
        first = _make("cache-b", LeadStatus.HOT)
        second = _make("cache-b", LeadStatus.WARM)  # different status — but cached
        assert first == second

    def test_cache_size_increments(self):
        before = cache_size()
        _make("cache-c", LeadStatus.COLD)
        assert cache_size() >= before + 1

    def test_clear_cache_removes_entry(self):
        _make("cache-c", LeadStatus.COLD)
        clear_cache("cache-c")
        assert get_cached_diagram("cache-c") is None

    def test_get_cached_diagram_returns_none_when_missing(self):
        assert get_cached_diagram("nonexistent-call-xyz") is None
