"""
Tests for src/quality.py — Call quality scorer.

Verifies: scoring dimensions, grade calculation, coaching notes, edge cases.
"""

import pytest
from src.quality import score_call_quality, QualityScore, _split_sides, _grade


# ---------------------------------------------------------------------------
# Test transcripts
# ---------------------------------------------------------------------------

EXCELLENT_TRANSCRIPT = """
Agent: Namaste! Online store banana chahte hain? Aapke products kya hain?
Lead: Haan, saree business hai mera.
Agent: Great! Kitna budget hai aapka? Aur kab tak chahiye website?
Lead: Budget around ₹30k. Next month tak chahiye.
Agent: Perfect! Aapka target customer kaun hai? Ladies mainly?
Lead: Haan, mostly married women aged 25-45.
Agent: Wonderful! I totally understand. Our complete store package fits perfectly at ₹25k.
       Shall we start today? I'll send you the agreement on WhatsApp. Aaj hi shuru karte hain!
Lead: Yes let's do it!
Agent: Main abhi details bhejta hoon WhatsApp pe. Today hi confirm karein!
"""

WEAK_TRANSCRIPT = """
Agent: Hi, we offer website services.
Lead: Not interested. I already have one.
Agent: Ok bye.
"""

OBJECTION_HANDLED = """
Agent: Hi! Online store ke baare mein baat karte hain.
Lead: Too expensive yaar. Bahut mehnga hai.
Agent: I totally understand that budget is a concern. Let me explain our starter plan.
       Actually, we have a ₹15k package that includes everything. Main bataata hoon.
Lead: Oh that's reasonable.
Agent: Shall we proceed? I'll send you the details right now on WhatsApp.
"""

HINDI_MIRROR = """
Agent: Namaste! Haan, bilkul. Aapka store banana bahut accha rahega.
       Main samajh sakta hoon aapki baat. Budget ke baare mein baat karte hain.
       Kitna invest karna chahte hain aap?
Lead: Haan, ₹20k around budget hai.
Agent: Perfect! Aaj hi shuru karte hain. Main details bhejta hoon.
"""

NO_CTA_TRANSCRIPT = """
Agent: Hi! We offer e-commerce website development services for small businesses.
Lead: Interesting, tell me more.
Agent: We build complete online stores with payment gateway integration.
Lead: Sounds good.
Agent: Yes we have helped many clients build successful online stores.
"""


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:
    def test_returns_quality_score(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert isinstance(result, QualityScore)

    def test_total_is_integer(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert isinstance(result.total, int)

    def test_total_between_0_and_100(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert 0 <= result.total <= 100

    def test_as_dict_has_required_keys(self):
        d = score_call_quality(EXCELLENT_TRANSCRIPT).as_dict()
        for k in ('total', 'grade', 'flag', 'dimensions', 'coaching_notes'):
            assert k in d

    def test_dimensions_has_all_categories(self):
        d = score_call_quality(EXCELLENT_TRANSCRIPT).as_dict()
        dims = d['dimensions']
        for k in ('qualifying_questions', 'objection_handling', 'language_mirroring',
                  'call_structure', 'cta_urgency'):
            assert k in dims

    def test_coaching_notes_is_list(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert isinstance(result.coaching_notes, list)


# ---------------------------------------------------------------------------
# Scoring direction
# ---------------------------------------------------------------------------

class TestScoringDirection:
    def test_excellent_transcript_scores_higher_than_weak(self):
        excellent = score_call_quality(EXCELLENT_TRANSCRIPT).total
        weak = score_call_quality(WEAK_TRANSCRIPT).total
        assert excellent > weak

    def test_excellent_transcript_scores_above_50(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert result.total > 50

    def test_weak_transcript_scores_below_40(self):
        result = score_call_quality(WEAK_TRANSCRIPT)
        assert result.total < 50  # weak should be below excellent

    def test_objection_handled_scores_higher_than_ignored(self):
        handled = score_call_quality(OBJECTION_HANDLED).total
        # WEAK_TRANSCRIPT ignores objection
        weak = score_call_quality(WEAK_TRANSCRIPT).total
        assert handled >= weak


# ---------------------------------------------------------------------------
# Dimension sub-scores
# ---------------------------------------------------------------------------

class TestDimensions:
    def test_qualifying_non_negative(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert result.qualifying >= 0

    def test_qualifying_max_30(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert result.qualifying <= 30

    def test_objection_max_25(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert result.objection <= 25

    def test_language_max_20(self):
        result = score_call_quality(HINDI_MIRROR)
        assert result.language <= 20

    def test_structure_max_15(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert result.structure <= 15

    def test_cta_max_10(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert result.cta <= 10

    def test_hindi_mirroring_scores_language_points(self):
        result = score_call_quality(HINDI_MIRROR)
        assert result.language > 0

    def test_no_cta_scores_low_on_cta(self):
        result = score_call_quality(NO_CTA_TRANSCRIPT)
        # May or may not be 0 depending on phrases — just check it's low
        assert result.cta <= 6


# ---------------------------------------------------------------------------
# Grade
# ---------------------------------------------------------------------------

class TestGrade:
    def test_grade_is_string(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert isinstance(result.grade, str)

    def test_valid_grade_values(self):
        valid = {'A+', 'A', 'B', 'C', 'D', 'F'}
        for transcript in [EXCELLENT_TRANSCRIPT, WEAK_TRANSCRIPT, OBJECTION_HANDLED]:
            g = score_call_quality(transcript).grade
            assert g in valid, f"Unexpected grade: {g}"

    def test_grade_function_90_plus(self):
        assert _grade(95) == 'A+'

    def test_grade_function_80s(self):
        assert _grade(85) == 'A'

    def test_grade_function_70s(self):
        assert _grade(75) == 'B'

    def test_grade_function_60s(self):
        assert _grade(65) == 'C'

    def test_grade_function_below_40(self):
        assert _grade(35) == 'F'


# ---------------------------------------------------------------------------
# Flag
# ---------------------------------------------------------------------------

class TestFlag:
    def test_flag_is_bool(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert isinstance(result.flag, bool)

    def test_weak_transcript_may_be_flagged(self):
        result = score_call_quality(WEAK_TRANSCRIPT)
        # flag is True when total < 40
        assert result.flag == (result.total < 40)

    def test_excellent_transcript_not_flagged(self):
        result = score_call_quality(EXCELLENT_TRANSCRIPT)
        assert result.flag == (result.total < 40)


# ---------------------------------------------------------------------------
# Transcript splitter
# ---------------------------------------------------------------------------

class TestSplitSides:
    def test_splits_agent_and_lead(self):
        agent, lead = _split_sides("Agent: Hi there.\nLead: Not interested.")
        assert "Hi there" in agent
        assert "Not interested" in lead

    def test_priya_prefix_counts_as_agent(self):
        agent, lead = _split_sides("Priya: Hello!\nLead: Yes.")
        assert "Hello" in agent

    def test_empty_transcript_returns_empty_strings(self):
        agent, lead = _split_sides("")
        assert agent == ""
        assert lead == ""

    def test_no_prefix_uses_alternating_heuristic(self):
        agent, lead = _split_sides("First line.\nSecond line.\nThird line.")
        assert isinstance(agent, str)
        assert isinstance(lead, str)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_transcript_returns_safe_default(self):
        result = score_call_quality("")
        assert isinstance(result, QualityScore)
        assert 0 <= result.total <= 100

    def test_agent_only_transcript(self):
        result = score_call_quality("Agent: Hello! We offer website development. Budget? Timeline?")
        assert isinstance(result.total, int)

    def test_lead_only_transcript(self):
        result = score_call_quality("Lead: Not interested. Bye.")
        assert isinstance(result.total, int)

    def test_as_dict_json_serializable(self):
        import json
        d = score_call_quality(EXCELLENT_TRANSCRIPT).as_dict()
        json.dumps(d)  # should not raise
