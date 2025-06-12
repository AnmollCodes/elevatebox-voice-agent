"""
Tests for src/persona.py — real-time buyer persona detection.

Validates:
  - Correct persona is detected from representative transcripts
  - Confidence scores are reasonable
  - Coaching instructions are non-empty and persona-specific
  - Follow-up WhatsApp angles are persona-appropriate
  - Edge cases: no signals, mixed signals, short transcripts
  - Multilingual signal detection (Hindi + Telugu)
"""

import pytest

from src.persona import (
    BuyerPersona,
    PersonaResult,
    build_adapt_prompt_response,
    detect_persona,
    get_followup_angle,
)


# ---------------------------------------------------------------------------
# Executive persona
# ---------------------------------------------------------------------------

class TestExecutivePersona:
    def test_credibility_question_is_executive(self):
        result = detect_persona("Who else have you built for? Do you have examples?")
        assert result.persona == BuyerPersona.EXECUTIVE

    def test_fast_decision_is_executive(self):
        result = detect_persona("Can we close this today? My team will handle the content.")
        assert result.persona == BuyerPersona.EXECUTIVE

    def test_scale_mention_is_executive(self):
        result = detect_persona("We have multiple branches and we're expanding. Can it scale?")
        assert result.persona == BuyerPersona.EXECUTIVE

    def test_hindi_executive_signal(self):
        result = detect_persona("Aur kisko banaya hai? Business badega kya?")
        assert result.persona == BuyerPersona.EXECUTIVE

    def test_executive_coaching_mentions_roi(self):
        result = detect_persona("Who else have you built for? Can we close this today?")
        assert "ROI" in result.coaching_instruction or "business" in result.coaching_instruction.lower()


# ---------------------------------------------------------------------------
# Explorer persona
# ---------------------------------------------------------------------------

class TestExplorerPersona:
    def test_feature_questions_are_explorer(self):
        result = detect_persona(
            "Does it support multiple payment gateways? Can it do product variants? "
            "What technology stack do you use?"
        )
        assert result.persona == BuyerPersona.EXPLORER

    def test_comparison_shopping_is_explorer(self):
        result = detect_persona("Is it better than Shopify? How does it compare to WooCommerce?")
        assert result.persona == BuyerPersona.EXPLORER

    def test_still_researching_is_explorer(self):
        result = detect_persona("I'm still exploring and comparing options right now.")
        assert result.persona == BuyerPersona.EXPLORER

    def test_hindi_explorer_signal(self):
        result = detect_persona("Kaise kaam karta hai? Kya features hain?")
        assert result.persona == BuyerPersona.EXPLORER

    def test_explorer_coaching_mentions_breadth(self):
        result = detect_persona("Does it support multiple gateways? How does it work?")
        assert any(w in result.coaching_instruction.lower()
                   for w in ["features", "breadth", "covers", "payments", "catalog"])


# ---------------------------------------------------------------------------
# Budget-constrained persona
# ---------------------------------------------------------------------------

class TestBudgetPersona:
    def test_price_first_is_budget(self):
        result = detect_persona("Tell me the cost first. How much does it cost?")
        assert result.persona == BuyerPersona.BUDGET

    def test_tight_budget_is_budget(self):
        result = detect_persona("Budget is limited. Is there any discount available?")
        assert result.persona == BuyerPersona.BUDGET

    def test_emi_request_is_budget(self):
        result = detect_persona("Can I pay in parts? Is there an EMI option?")
        assert result.persona == BuyerPersona.BUDGET

    def test_hindi_budget_signal(self):
        result = detect_persona("Kitna lagega? Kuch discount milega? Budget kam hai.")
        assert result.persona == BuyerPersona.BUDGET

    def test_telugu_budget_signal(self):
        result = detect_persona("Yentha agatundi? Discount untunda? Budget takkuva.")
        assert result.persona == BuyerPersona.BUDGET

    def test_budget_coaching_mentions_price(self):
        result = detect_persona("How much does it cost? Any discount?")
        assert "15,000" in result.coaching_instruction or "₹" in result.coaching_instruction


# ---------------------------------------------------------------------------
# Time-pressured persona
# ---------------------------------------------------------------------------

class TestTimePressuredPersona:
    def test_deadline_is_time_pressured(self):
        result = detect_persona("I need it by next month. In how many days can you deliver?")
        assert result.persona == BuyerPersona.TIME_PRESSURED

    def test_festival_deadline_is_time_pressured(self):
        result = detect_persona("I want to launch before Diwali. Can you do it faster?")
        assert result.persona == BuyerPersona.TIME_PRESSURED

    def test_asap_is_time_pressured(self):
        result = detect_persona("I need it as soon as possible. Is there a rush option?")
        assert result.persona == BuyerPersona.TIME_PRESSURED

    def test_hindi_time_pressured(self):
        result = detect_persona("Jaldi chahiye! Diwali se pehle chahiye. Kitne din mein?")
        assert result.persona == BuyerPersona.TIME_PRESSURED

    def test_telugu_time_pressured(self):
        result = detect_persona("Twaraga kavali, pandaga mundu ready avvali.")
        assert result.persona == BuyerPersona.TIME_PRESSURED

    def test_time_pressured_coaching_mentions_delivery(self):
        result = detect_persona("I need it as soon as possible, need it by next week.")
        assert "14" in result.coaching_instruction or "days" in result.coaching_instruction.lower()


# ---------------------------------------------------------------------------
# Unknown / low confidence
# ---------------------------------------------------------------------------

class TestUnknownPersona:
    def test_empty_transcript_is_unknown(self):
        result = detect_persona("")
        assert result.persona == BuyerPersona.UNKNOWN

    def test_greeting_only_is_unknown(self):
        result = detect_persona("Hello, yes I'm here. Thanks for calling.")
        assert result.persona == BuyerPersona.UNKNOWN

    def test_unknown_confidence_is_low(self):
        result = detect_persona("Ok bye.")
        assert result.confidence <= 0.35

    def test_unknown_coaching_instructs_discovery(self):
        result = detect_persona("")
        assert "question" in result.coaching_instruction.lower() or "discover" in result.coaching_instruction.lower()


# ---------------------------------------------------------------------------
# PersonaResult properties
# ---------------------------------------------------------------------------

class TestPersonaResult:
    def test_is_confident_threshold(self):
        high = PersonaResult(persona=BuyerPersona.EXECUTIVE, confidence=0.75, coaching_instruction="x")
        low = PersonaResult(persona=BuyerPersona.UNKNOWN, confidence=0.30, coaching_instruction="x")
        assert high.is_confident is True
        assert low.is_confident is False

    def test_str_representation(self):
        result = PersonaResult(persona=BuyerPersona.BUDGET, confidence=0.80, coaching_instruction="x")
        assert "budget" in str(result)
        assert "80%" in str(result)


# ---------------------------------------------------------------------------
# build_adapt_prompt_response
# ---------------------------------------------------------------------------

class TestBuildAdaptPromptResponse:
    def test_returns_result_key(self):
        response = build_adapt_prompt_response("How much does it cost? Any discount?")
        assert "result" in response
        assert isinstance(response["result"], str)
        assert len(response["result"]) > 10

    def test_returns_persona_detected(self):
        response = build_adapt_prompt_response("Can we close this today?")
        assert "persona_detected" in response

    def test_returns_confidence(self):
        response = build_adapt_prompt_response("not interested at all")
        assert "confidence" in response
        assert 0.0 <= response["confidence"] <= 1.0

    def test_returns_signals_matched_count(self):
        response = build_adapt_prompt_response("How much does it cost? Budget is limited.")
        assert "signals_matched" in response
        assert response["signals_matched"] >= 1


# ---------------------------------------------------------------------------
# get_followup_angle
# ---------------------------------------------------------------------------

class TestFollowupAngles:
    def test_executive_angle_mentions_architecture(self):
        angle = get_followup_angle(BuyerPersona.EXECUTIVE)
        assert "architecture" in angle.lower() or "technical" in angle.lower()

    def test_budget_angle_mentions_price(self):
        angle = get_followup_angle(BuyerPersona.BUDGET)
        assert "₹" in angle or "15,000" in angle

    def test_time_pressured_angle_mentions_timeline(self):
        angle = get_followup_angle(BuyerPersona.TIME_PRESSURED)
        assert "14 days" in angle or "timeline" in angle.lower() or "live" in angle.lower()

    def test_all_personas_have_angles(self):
        for persona in BuyerPersona:
            angle = get_followup_angle(persona)
            assert isinstance(angle, str) and len(angle) > 10
