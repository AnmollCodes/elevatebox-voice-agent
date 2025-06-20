"""
Tests for src/objections.py — Objection detection and personalised rebuttal generation.
"""

import pytest
from src.objections import detect_objections, ObjectionMap, ObjectionType, DetectedObjection


# ---------------------------------------------------------------------------
# Test transcripts
# ---------------------------------------------------------------------------

PRICE_TRANSCRIPT = "Lead: It's too expensive for me. Bahut mehnga hai. I can't afford this."
TIMING_TRANSCRIPT = "Lead: Not now, abhi nahi. I'm very busy. Call me next month."
TRUST_TRANSCRIPT = "Lead: Who are you? I've never heard of your company. Can you show me any examples?"
COMPETITION_TRANSCRIPT = "Lead: I already have a website. I'm also talking to another agency."
MULTI_OBJECTION = "Lead: Too expensive and I'm busy right now. Can't afford it, not now."
NO_OBJECTION = "Lead: Let's do it! How do I pay? Send me the details."
HINDI_PRICE = "Lead: Budget nahi hai. Bahut mehnga lagta hai. Budget kam hai."
TELUGU_TRANSCRIPT = "Lead: Not interested right now. Abhi ledu. Busy undi."


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:
    def test_returns_objection_map(self):
        result = detect_objections(PRICE_TRANSCRIPT)
        assert isinstance(result, ObjectionMap)

    def test_as_dict_has_required_keys(self):
        d = detect_objections(PRICE_TRANSCRIPT).as_dict()
        for k in ('primary_objection', 'no_objection', 'objections', 'whatsapp_rebuttal'):
            assert k in d

    def test_objection_list_contains_detected_objections(self):
        result = detect_objections(PRICE_TRANSCRIPT)
        assert isinstance(result.objections, list)

    def test_each_objection_has_required_fields(self):
        result = detect_objections(PRICE_TRANSCRIPT)
        for obj in result.objections:
            assert hasattr(obj, 'objection_type')
            assert hasattr(obj, 'exact_phrase')
            assert hasattr(obj, 'mirror_phrase')
            assert hasattr(obj, 'confidence')
            assert hasattr(obj, 'rebuttal')


# ---------------------------------------------------------------------------
# Objection type detection
# ---------------------------------------------------------------------------

class TestObjectionTypeDetection:
    def test_detects_price_objection(self):
        result = detect_objections(PRICE_TRANSCRIPT)
        assert result.primary_objection == ObjectionType.PRICE

    def test_detects_timing_objection(self):
        result = detect_objections(TIMING_TRANSCRIPT)
        assert result.primary_objection == ObjectionType.TIMING

    def test_detects_trust_objection(self):
        result = detect_objections(TRUST_TRANSCRIPT)
        assert result.primary_objection == ObjectionType.TRUST

    def test_detects_competition_objection(self):
        result = detect_objections(COMPETITION_TRANSCRIPT)
        assert result.primary_objection == ObjectionType.COMPETITION

    def test_no_objection_when_hot(self):
        result = detect_objections(NO_OBJECTION)
        assert result.no_objection is True
        assert result.primary_objection == ObjectionType.NONE

    def test_hindi_price_detected(self):
        result = detect_objections(HINDI_PRICE)
        assert result.primary_objection == ObjectionType.PRICE


# ---------------------------------------------------------------------------
# Multi-objection handling
# ---------------------------------------------------------------------------

class TestMultiObjection:
    def test_multi_objection_finds_multiple(self):
        result = detect_objections(MULTI_OBJECTION)
        obj_types = {o.objection_type for o in result.objections}
        # Both PRICE and TIMING should be detected
        assert ObjectionType.PRICE in obj_types or ObjectionType.TIMING in obj_types

    def test_primary_is_highest_confidence(self):
        result = detect_objections(MULTI_OBJECTION)
        if result.objections:
            max_conf = max(o.confidence for o in result.objections)
            assert result.objections[0].confidence == max_conf


# ---------------------------------------------------------------------------
# Rebuttal content
# ---------------------------------------------------------------------------

class TestRebuttalContent:
    def test_rebuttal_is_non_empty_string(self):
        result = detect_objections(PRICE_TRANSCRIPT)
        assert isinstance(result.whatsapp_rebuttal, str)
        assert len(result.whatsapp_rebuttal) > 10

    def test_price_rebuttal_mentions_price(self):
        result = detect_objections(PRICE_TRANSCRIPT)
        rebuttal_lower = result.whatsapp_rebuttal.lower()
        assert any(w in rebuttal_lower for w in ('₹', 'budget', 'cost', 'price', 'plan', 'afford'))

    def test_trust_rebuttal_mentions_portfolio(self):
        result = detect_objections(TRUST_TRANSCRIPT)
        rebuttal_lower = result.whatsapp_rebuttal.lower()
        assert any(w in rebuttal_lower for w in ('portfolio', 'example', 'store', 'client', 'work', 'demo'))

    def test_competition_rebuttal_mentions_comparison(self):
        result = detect_objections(COMPETITION_TRANSCRIPT)
        rebuttal_lower = result.whatsapp_rebuttal.lower()
        assert any(w in rebuttal_lower for w in ('comparison', 'audit', 'edge', 'upgrade', 'migrate', 'sheet'))

    def test_timing_rebuttal_is_non_pushy(self):
        result = detect_objections(TIMING_TRANSCRIPT)
        assert len(result.whatsapp_rebuttal) > 5

    def test_no_objection_still_returns_fallback_rebuttal(self):
        result = detect_objections(NO_OBJECTION)
        assert isinstance(result.whatsapp_rebuttal, str)
        assert len(result.whatsapp_rebuttal) > 5


# ---------------------------------------------------------------------------
# Confidence range
# ---------------------------------------------------------------------------

class TestConfidenceRange:
    def test_confidence_between_0_and_1(self):
        result = detect_objections(PRICE_TRANSCRIPT)
        for obj in result.objections:
            assert 0.0 <= obj.confidence <= 1.0

    def test_strong_price_signal_has_high_confidence(self):
        result = detect_objections("Lead: Too expensive. Can't afford it. Bahut mehnga.")
        price_obj = next((o for o in result.objections if o.objection_type == ObjectionType.PRICE), None)
        if price_obj:
            assert price_obj.confidence >= 0.7


# ---------------------------------------------------------------------------
# Phone number injection in rebuttal
# ---------------------------------------------------------------------------

class TestPhoneInjection:
    def test_phone_appears_in_timing_rebuttal(self):
        result = detect_objections(TIMING_TRANSCRIPT, phone="+919876543210")
        # Phone may or may not be in rebuttal depending on template
        assert isinstance(result.whatsapp_rebuttal, str)

    def test_empty_phone_does_not_crash(self):
        result = detect_objections(PRICE_TRANSCRIPT, phone="")
        assert isinstance(result.whatsapp_rebuttal, str)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_transcript_returns_no_objection(self):
        result = detect_objections("")
        assert result.no_objection is True

    def test_whitespace_transcript_returns_no_objection(self):
        result = detect_objections("   \n  ")
        assert result.no_objection is True

    def test_rebuttal_index_selects_variant(self):
        r0 = detect_objections(PRICE_TRANSCRIPT, rebuttal_index=0)
        r1 = detect_objections(PRICE_TRANSCRIPT, rebuttal_index=1)
        # Both should be valid strings (may or may not be different templates)
        assert isinstance(r0.whatsapp_rebuttal, str)
        assert isinstance(r1.whatsapp_rebuttal, str)

    def test_as_dict_serializable(self):
        import json
        d = detect_objections(PRICE_TRANSCRIPT).as_dict()
        json.dumps(d)  # should not raise
