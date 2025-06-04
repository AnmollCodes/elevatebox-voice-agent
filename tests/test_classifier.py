"""
Tests for lead classification logic.

These tests are pure unit tests — no external calls, no mocks needed.
They verify that the signal-based classifier makes the right call on
realistic transcript samples including mixed-language inputs.
"""

import pytest

from src.classifier import (
    LeadStatus,
    classify_from_transcript,
    extract_budget_from_transcript,
)


class TestHotLeadClassification:
    """HOT lead signals: clear buying intent, asks for price or timeline."""

    def test_send_details_phrase(self):
        transcript = "Yeah, send me the details. How soon can you deliver?"
        assert classify_from_transcript(transcript) == LeadStatus.HOT

    def test_lets_do_it(self):
        transcript = "Sounds good, let's do it. When can we start?"
        assert classify_from_transcript(transcript) == LeadStatus.HOT

    def test_how_much_does_it_cost(self):
        transcript = "Okay I like what you're saying. How much does it cost for 200 products?"
        assert classify_from_transcript(transcript) == LeadStatus.HOT

    def test_hindi_hot_signal(self):
        transcript = "Haan, kab shuru kar sakte hain? Kitna lagega roughly?"
        assert classify_from_transcript(transcript) == LeadStatus.HOT

    def test_hindi_price_inquiry(self):
        transcript = "price batao, hum kar lete hain"
        assert classify_from_transcript(transcript) == LeadStatus.HOT

    def test_when_can_you_start(self):
        transcript = "I sell handmade jewellery. When can you start?"
        assert classify_from_transcript(transcript) == LeadStatus.HOT

    def test_ready_to_proceed(self):
        transcript = "We have budget approved. Ready to proceed, what's next?"
        assert classify_from_transcript(transcript) == LeadStatus.HOT

    def test_telugu_details_send(self):
        transcript = "Details pampu, chadutha. Bayam ledu."
        assert classify_from_transcript(transcript) == LeadStatus.HOT


class TestWarmLeadClassification:
    """WARM leads: interested but something is blocking them."""

    def test_budget_tight(self):
        transcript = "I like it but budget is tight right now. Maybe next quarter."
        assert classify_from_transcript(transcript) == LeadStatus.WARM

    def test_needs_partner_approval(self):
        transcript = "Sounds interesting, but I need to discuss with my partner first."
        assert classify_from_transcript(transcript) == LeadStatus.WARM

    def test_hindi_not_now(self):
        transcript = "Abhi nahi, baad mein baat karte hain. Abhi budget nahi hai."
        assert classify_from_transcript(transcript) == LeadStatus.WARM

    def test_considering(self):
        transcript = "I am considering it. Let me think about the timeline."
        assert classify_from_transcript(transcript) == LeadStatus.WARM

    def test_callback_request(self):
        transcript = "Call me back tomorrow, I need to check with my brother."
        assert classify_from_transcript(transcript) == LeadStatus.WARM

    def test_hindi_callback(self):
        transcript = "Kal baat karte hain, partner se poochna padega."
        assert classify_from_transcript(transcript) == LeadStatus.WARM

    def test_telugu_later(self):
        transcript = "Ippudu kadu, taruvata cheppandi."
        assert classify_from_transcript(transcript) == LeadStatus.WARM


class TestColdLeadClassification:
    """COLD leads: not interested or just exploring."""

    def test_not_interested(self):
        transcript = "Not interested, I already have a website that works fine."
        assert classify_from_transcript(transcript) == LeadStatus.COLD

    def test_just_checking(self):
        transcript = "I was just checking prices, no urgent need right now."
        assert classify_from_transcript(transcript) == LeadStatus.COLD

    def test_hindi_no_need(self):
        transcript = "Zaroorat nahi hai, pehle se hai website."
        assert classify_from_transcript(transcript) == LeadStatus.COLD

    def test_no_budget(self):
        transcript = "No budget for this, not interested at all."
        assert classify_from_transcript(transcript) == LeadStatus.COLD

    def test_telugu_no_need(self):
        transcript = "Avasaram ledu, thank you."
        assert classify_from_transcript(transcript) == LeadStatus.COLD


class TestEdgeCases:
    """Edge cases: empty transcripts, ambiguous signals, mixed language."""

    def test_empty_transcript_defaults_to_warm(self):
        """Defaulting to WARM on no signal is safer than discarding a lead."""
        assert classify_from_transcript("") == LeadStatus.WARM

    def test_just_greeting_defaults_to_warm(self):
        transcript = "Hello, yes this is Ravi speaking."
        assert classify_from_transcript(transcript) == LeadStatus.WARM

    def test_mixed_english_telugu_hot(self):
        """Code-switching is very common in Hyderabad — must handle correctly."""
        transcript = "Send me the details garu. Price yentha agatundi?"
        assert classify_from_transcript(transcript) == LeadStatus.HOT

    def test_conflicting_signals_hot_wins(self):
        """If hot and warm signals both appear, hot intent takes priority."""
        transcript = "Budget is tight but let's do it anyway, how much does it cost?"
        # HOT signals outweigh WARM here
        result = classify_from_transcript(transcript)
        assert result == LeadStatus.HOT


class TestBudgetExtraction:
    """Budget extraction from transcript text."""

    def test_rupee_symbol(self):
        result = extract_budget_from_transcript("I have a budget of ₹50,000")
        assert result is not None
        assert "50,000" in result or "₹" in result

    def test_lakh_mention(self):
        result = extract_budget_from_transcript("maybe 1.5 lakh rupees")
        assert result is not None

    def test_rs_prefix(self):
        result = extract_budget_from_transcript("Around rs. 30000 I think")
        assert result is not None

    def test_no_budget_mentioned(self):
        result = extract_budget_from_transcript("I need a nice website with good design")
        assert result is None

    def test_k_notation(self):
        result = extract_budget_from_transcript("budget around 20k")
        assert result is not None
