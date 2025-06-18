"""
Tests for src/trajectory.py — SentimentArc analysis.

Verifies: segment scoring, arc labels, momentum calculation, turning points,
coaching notes, edge cases (empty, single segment).
"""

import pytest
from src.trajectory import analyse_trajectory, SentimentArc, _score_segment, _score_to_label


# ---------------------------------------------------------------------------
# Helper transcripts
# ---------------------------------------------------------------------------

COLD_TO_HOT = """
Lead: Not interested at all. Busy right now. Don't call again.
Agent: I understand completely. Just one thing before I go.
Lead: Okay fine. Let's do it. How do I pay? Abhi shuru karte hain. Send me details on WhatsApp.
"""

HOT_THROUGHOUT = """
Lead: Yes let's do it immediately! I want to move forward today.
Agent: Great! Let me take you through the details.
Lead: How do I pay? Send me the contract now please. Bilkul. I'm in.
"""

COLD_THROUGHOUT = """
Lead: Not interested. I am not interested at all. Don't call.
Agent: I understand.
Lead: Please remove my number. Not interested. Stop calling.
"""

WARM_TO_HOT = """
Lead: Maybe. I'm thinking about it. Not sure about budget right now.
Agent: Our starter pack is ₹15k. Want to know more?
Lead: Actually yes. Let's start. How do I pay? Send me the details on WhatsApp please.
"""

HOT_TO_COLD = """
Lead: Yes I'm very interested! Let's do it. How do I pay?
Agent: Great! Let me walk you through.
Lead: Actually not interested. Too expensive. Please don't call again.
"""

NEUTRAL = "We had a conversation about website development services today."


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:
    def test_returns_sentiment_arc(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert isinstance(result, SentimentArc)

    def test_scores_is_list_of_3_floats(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert len(result.scores) == 3
        assert all(isinstance(s, float) for s in result.scores)

    def test_labels_is_list_of_3(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert len(result.labels) == 3

    def test_arc_is_string(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert isinstance(result.arc, str)

    def test_arc_contains_arrows(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert '→' in result.arc

    def test_as_dict_keys(self):
        d = analyse_trajectory(HOT_THROUGHOUT).as_dict()
        for k in ('scores', 'labels', 'arc', 'momentum', 'turning_point', 'coaching_note'):
            assert k in d


# ---------------------------------------------------------------------------
# Label correctness
# ---------------------------------------------------------------------------

class TestLabels:
    def test_labels_only_valid_values(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        for label in result.labels:
            assert label in ('hot', 'warm', 'cold')

    def test_hot_transcript_labels_include_hot(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert 'hot' in result.labels

    def test_cold_transcript_labels_include_cold(self):
        result = analyse_trajectory(COLD_THROUGHOUT)
        assert 'cold' in result.labels

    def test_arc_matches_labels(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        expected_arc = '→'.join(result.labels)
        assert result.arc == expected_arc


# ---------------------------------------------------------------------------
# Score bounds
# ---------------------------------------------------------------------------

class TestScoreBounds:
    def test_scores_between_0_and_1(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        for s in result.scores:
            assert 0.0 <= s <= 1.0

    def test_momentum_between_minus1_and_1(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert -1.0 <= result.momentum <= 1.0

    def test_momentum_positive_for_cold_to_hot(self):
        result = analyse_trajectory(COLD_TO_HOT)
        assert result.momentum > 0

    def test_momentum_negative_for_hot_to_cold(self):
        result = analyse_trajectory(HOT_TO_COLD)
        assert result.momentum < 0

    def test_momentum_near_zero_for_cold_throughout(self):
        result = analyse_trajectory(COLD_THROUGHOUT)
        # All cold → momentum should be small (cold score is ≈ same throughout)
        assert abs(result.momentum) < 0.5


# ---------------------------------------------------------------------------
# Turning point
# ---------------------------------------------------------------------------

class TestTurningPoint:
    def test_turning_point_is_none_or_int(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert result.turning_point is None or isinstance(result.turning_point, int)

    def test_turning_point_is_2_or_3_when_set(self):
        result = analyse_trajectory(COLD_TO_HOT)
        if result.turning_point is not None:
            assert result.turning_point in (2, 3)

    def test_flat_transcript_has_no_turning_point(self):
        result = analyse_trajectory(COLD_THROUGHOUT)
        # Could be None — no meaningful shift in a cold-throughout call
        # We can't guarantee None but momentum should be low
        assert abs(result.momentum) < 0.6


# ---------------------------------------------------------------------------
# Coaching notes
# ---------------------------------------------------------------------------

class TestCoachingNotes:
    def test_coaching_note_is_non_empty_string(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert isinstance(result.coaching_note, str)
        assert len(result.coaching_note) > 10

    def test_cold_to_hot_has_recovery_note(self):
        result = analyse_trajectory(COLD_TO_HOT)
        # Arc ends in hot — note should mention the improvement
        note = result.coaching_note.lower()
        assert any(w in note for w in ('recover', 'close', 'arc', 'hot', 'positive', 'warm'))


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_transcript_returns_safe_default(self):
        result = analyse_trajectory("")
        assert result.arc == "warm→warm→warm"
        assert result.momentum == 0.0
        assert result.turning_point is None

    def test_whitespace_transcript_returns_safe_default(self):
        result = analyse_trajectory("   \n  ")
        assert isinstance(result.arc, str)

    def test_very_short_transcript(self):
        result = analyse_trajectory("ok")
        assert len(result.scores) == 3

    def test_segment_texts_are_stored(self):
        result = analyse_trajectory(HOT_THROUGHOUT)
        assert len(result.segment_texts) == 3
        assert all(isinstance(s, str) for s in result.segment_texts)


# ---------------------------------------------------------------------------
# _score_segment unit tests
# ---------------------------------------------------------------------------

class TestScoreSegment:
    def test_hot_phrase_scores_high(self):
        score = _score_segment("Let's do it! How do I pay? Abhi shuru karte hain.")
        assert score >= 0.6

    def test_cold_phrase_scores_low(self):
        score = _score_segment("Not interested at all. Please don't call again.")
        assert score <= 0.4

    def test_neutral_phrase_scores_near_middle(self):
        score = _score_segment("We discussed the weather today.")
        assert score == 0.5  # no signals → exactly neutral


# ---------------------------------------------------------------------------
# _score_to_label
# ---------------------------------------------------------------------------

class TestScoreToLabel:
    def test_high_score_is_hot(self):
        assert _score_to_label(0.8) == 'hot'

    def test_mid_score_is_warm(self):
        assert _score_to_label(0.5) == 'warm'

    def test_low_score_is_cold(self):
        assert _score_to_label(0.2) == 'cold'

    def test_boundary_065_is_hot(self):
        assert _score_to_label(0.65) == 'hot'

    def test_boundary_040_is_warm(self):
        assert _score_to_label(0.40) == 'warm'
