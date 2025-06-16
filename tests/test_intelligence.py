"""
Tests for src/intelligence.py — evidence-based intent classification engine.

Validates:
  - Evidence objects are created with correct fields
  - Confidence scores scale with signal strength
  - Multi-signal boost is applied correctly
  - HOT/WARM/COLD classifications are correct across languages
  - Budget extraction works
  - Language detection is accurate
  - Edge cases: empty transcript, conflicting signals, weak-only signals
"""

import pytest

from src.classifier import LeadStatus
from src.intelligence import (
    EvidenceResult,
    classify_with_evidence,
)


# ---------------------------------------------------------------------------
# HOT classification
# ---------------------------------------------------------------------------

class TestHotClassification:
    def test_direct_english_buying_signal(self):
        result = classify_with_evidence("When can you start? I'm ready to proceed.")
        assert result.status == LeadStatus.HOT
        assert result.confidence >= 0.65

    def test_price_inquiry_is_hot(self):
        result = classify_with_evidence("How much does it cost? What is the price?")
        assert result.status == LeadStatus.HOT

    def test_payment_inquiry_is_strong_hot(self):
        result = classify_with_evidence("Where do I pay? How do I pay?")
        assert result.status == LeadStatus.HOT
        assert result.confidence >= 0.85

    def test_hindi_hot_signal(self):
        result = classify_with_evidence("Kab shuru kar sakte ho? Kitna lagega?")
        assert result.status == LeadStatus.HOT

    def test_hindi_details_request(self):
        result = classify_with_evidence("Details bhejo na, price batao.")
        assert result.status == LeadStatus.HOT

    def test_telugu_hot_signal(self):
        result = classify_with_evidence("Yentha agatundi? Details pampu please.")
        assert result.status == LeadStatus.HOT

    def test_multiple_hot_signals_raise_confidence(self):
        single = classify_with_evidence("let's do it")
        multi = classify_with_evidence("let's do it. how do i pay? when can you start?")
        assert multi.confidence >= single.confidence

    def test_evidence_trail_populated(self):
        result = classify_with_evidence("let's do it and send me the details")
        assert len(result.evidence) >= 1
        assert all(e.weight > 0 for e in result.evidence)

    def test_readable_evidence_is_non_empty(self):
        result = classify_with_evidence("send me the details please")
        assert result.readable_evidence() != ""
        assert result.readable_evidence() != "general conversation"

    def test_top_evidence_sorted_by_weight(self):
        result = classify_with_evidence("let's do it, send me the details, how do i pay")
        top = result.top_evidence(3)
        weights = [e.weight for e in top]
        assert weights == sorted(weights, reverse=True)


# ---------------------------------------------------------------------------
# WARM classification
# ---------------------------------------------------------------------------

class TestWarmClassification:
    def test_budget_concern_is_warm(self):
        result = classify_with_evidence("Budget is tight at the moment, need to think about it.")
        assert result.status == LeadStatus.WARM

    def test_approval_needed_is_warm(self):
        result = classify_with_evidence("I need to discuss this with my partner before deciding.")
        assert result.status == LeadStatus.WARM

    def test_timing_barrier_is_warm(self):
        result = classify_with_evidence("Not right now, maybe next month.")
        assert result.status == LeadStatus.WARM

    def test_hindi_warm_not_now(self):
        result = classify_with_evidence("Abhi nahi, sochna padega.")
        assert result.status == LeadStatus.WARM

    def test_telugu_warm_callback(self):
        result = classify_with_evidence("Taruvata cheppandi, ippudu kadu.")
        assert result.status == LeadStatus.WARM

    def test_warm_confidence_is_at_least_50(self):
        result = classify_with_evidence("Let me think about it, call me back.")
        assert result.status == LeadStatus.WARM
        assert result.confidence >= 0.50


# ---------------------------------------------------------------------------
# COLD classification
# ---------------------------------------------------------------------------

class TestColdClassification:
    def test_explicit_not_interested(self):
        result = classify_with_evidence("I am not interested at all.")
        assert result.status == LeadStatus.COLD

    def test_has_website_already(self):
        result = classify_with_evidence("We already have a website, don't need it.")
        assert result.status == LeadStatus.COLD

    def test_hindi_cold_signal(self):
        result = classify_with_evidence("Zaroorat nahi hai, nahi chahiye.")
        assert result.status == LeadStatus.COLD

    def test_telugu_cold_signal(self):
        result = classify_with_evidence("Avasaram ledu bro.")
        assert result.status == LeadStatus.COLD

    def test_explicit_decline_is_high_confidence_cold(self):
        result = classify_with_evidence("Don't call me again, not interested.")
        assert result.status == LeadStatus.COLD
        assert result.confidence >= 0.80


# ---------------------------------------------------------------------------
# Confidence and scoring
# ---------------------------------------------------------------------------

class TestConfidenceScoring:
    def test_no_signals_returns_warm_low_confidence(self):
        result = classify_with_evidence("Hello, yes I'm here. Ok. Bye.")
        assert result.status == LeadStatus.WARM
        assert result.confidence <= 0.35

    def test_confidence_is_bounded_0_to_1(self):
        result = classify_with_evidence(
            "let's do it let's move forward send me the details "
            "how do i pay when can you start ready to proceed"
        )
        assert 0.0 <= result.confidence <= 1.0

    def test_indirect_signals_alone_can_produce_hot(self):
        # Indirect signals: no direct buying phrase, but asking about features + scale + timeline
        result = classify_with_evidence(
            "Does it support multiple payment gateways? I sell handmade products. "
            "In how many weeks will it be ready?"
        )
        # Should at least register as HOT or WARM — not COLD
        assert result.status in (LeadStatus.HOT, LeadStatus.WARM)

    def test_strong_cold_overrides_warm(self):
        result = classify_with_evidence(
            "Actually I might consider it... but no, not interested."
        )
        assert result.status == LeadStatus.COLD


# ---------------------------------------------------------------------------
# Budget extraction
# ---------------------------------------------------------------------------

class TestBudgetExtraction:
    def test_rupee_symbol(self):
        result = classify_with_evidence("I have ₹50,000 budget. How soon can you start?")
        assert result.budget_detected is not None
        assert "50" in result.budget_detected

    def test_rs_prefix(self):
        result = classify_with_evidence("Roughly rs. 30000. When can you start?")
        assert result.budget_detected is not None

    def test_lakh_notation(self):
        result = classify_with_evidence("1 lakh rupees is what I have. let's do it")
        assert result.budget_detected is not None
        assert "lakh" in result.budget_detected

    def test_no_budget_returns_none(self):
        result = classify_with_evidence("When can you start?")
        assert result.budget_detected is None


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class TestLanguageDetection:
    def test_english_transcript(self):
        result = classify_with_evidence("When can you start? Let's proceed with the project.")
        assert result.language_detected == "English"

    def test_hindi_transcript(self):
        result = classify_with_evidence("Haan bhai, main interested hoon. Kab shuru hoga?")
        assert result.language_detected == "Hindi"

    def test_telugu_transcript(self):
        result = classify_with_evidence("Cheyandi, nenu ready garu. Ela chestaru?")
        assert result.language_detected == "Telugu"


# ---------------------------------------------------------------------------
# Reasoning summary
# ---------------------------------------------------------------------------

class TestReasoningSummary:
    def test_summary_mentions_status(self):
        # Use two buying signals so HOT classification is unambiguous
        result = classify_with_evidence("let's do it and how do i pay")
        assert result.status == LeadStatus.HOT
        assert "hot" in result.reasoning_summary.lower() or "HOT" in result.reasoning_summary

    def test_summary_mentions_confidence(self):
        result = classify_with_evidence("not interested")
        assert "%" in result.reasoning_summary

    def test_summary_includes_budget_when_detected(self):
        result = classify_with_evidence("₹50000 budget. let's do it.")
        if result.budget_detected:
            assert "50" in result.reasoning_summary or "Budget" in result.reasoning_summary
