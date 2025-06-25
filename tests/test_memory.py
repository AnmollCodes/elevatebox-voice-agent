"""
Tests for src/memory.py — LeadMemory across calls.

Verifies: storage, retrieval, history limits, context snippets, persona openers,
Redis fallback, singleton behavior.
"""

import time
import pytest
from src.memory import LeadMemory, LeadProfile, CallRecord


PHONE = "+919876543210"
PHONE2 = "+918888888888"


@pytest.fixture(autouse=True)
def fresh_memory():
    """Reset the global singleton before each test."""
    from src.memory import lead_memory
    lead_memory.clear()
    yield
    lead_memory.clear()


def make_memory() -> LeadMemory:
    """Fresh in-memory LeadMemory for each test."""
    m = LeadMemory()
    m.clear()
    return m


# ---------------------------------------------------------------------------
# Basic get/record
# ---------------------------------------------------------------------------

class TestBasicGetRecord:
    def test_unknown_phone_returns_empty_profile(self):
        m = make_memory()
        p = m.get(PHONE)
        assert isinstance(p, LeadProfile)
        assert p.phone == PHONE
        assert p.call_count == 0
        assert p.is_returning is False

    def test_record_increases_call_count(self):
        m = make_memory()
        m.record(PHONE, "call-1", "HOT", 0.9)
        assert m.get(PHONE).call_count == 1

    def test_record_twice_increments_to_2(self):
        m = make_memory()
        m.record(PHONE, "call-1", "HOT", 0.9)
        m.record(PHONE, "call-2", "WARM", 0.6)
        assert m.get(PHONE).call_count == 2

    def test_last_status_is_updated(self):
        m = make_memory()
        m.record(PHONE, "call-1", "HOT", 0.9)
        assert m.get(PHONE).last_status == "HOT"

    def test_last_objection_is_stored(self):
        m = make_memory()
        m.record(PHONE, "call-1", "WARM", 0.6, primary_objection="price")
        assert m.get(PHONE).last_objection == "price"

    def test_last_persona_is_stored(self):
        m = make_memory()
        m.record(PHONE, "call-1", "HOT", 0.9, persona="Executive")
        assert m.get(PHONE).last_persona == "Executive"

    def test_returns_updated_profile(self):
        m = make_memory()
        profile = m.record(PHONE, "call-1", "HOT", 0.9)
        assert isinstance(profile, LeadProfile)
        assert profile.call_count == 1


# ---------------------------------------------------------------------------
# is_returning
# ---------------------------------------------------------------------------

class TestIsReturning:
    def test_first_call_not_returning(self):
        m = make_memory()
        assert m.get(PHONE).is_returning is False

    def test_after_one_record_is_returning(self):
        m = make_memory()
        m.record(PHONE, "call-1", "HOT", 0.9)
        assert m.get(PHONE).is_returning is True

    def test_different_phone_not_returning(self):
        m = make_memory()
        m.record(PHONE, "call-1", "HOT", 0.9)
        assert m.get(PHONE2).is_returning is False


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class TestHistory:
    def test_history_grows_with_calls(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        m.record(PHONE, "c2", "WARM", 0.6)
        assert len(m.get(PHONE).history) == 2

    def test_history_capped_at_max(self):
        m = make_memory()
        for i in range(25):
            m.record(PHONE, f"c{i}", "WARM", 0.5)
        assert len(m.get(PHONE).history) <= LeadMemory.MAX_HISTORY

    def test_history_contains_call_records(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        hist = m.get(PHONE).history
        assert all(isinstance(r, CallRecord) for r in hist)

    def test_history_stores_call_id(self):
        m = make_memory()
        m.record(PHONE, "my-call-abc", "HOT", 0.9)
        assert m.get(PHONE).history[0].call_id == "my-call-abc"

    def test_history_stores_snippet(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9, transcript_snippet="Lead: Let's do it!")
        snippet = m.get(PHONE).history[0].transcript_snippet
        assert "Let's do it" in snippet

    def test_snippet_truncated_at_200_chars(self):
        m = make_memory()
        long = "x" * 500
        m.record(PHONE, "c1", "HOT", 0.9, transcript_snippet=long)
        assert len(m.get(PHONE).history[0].transcript_snippet) <= 200


# ---------------------------------------------------------------------------
# Context snippet
# ---------------------------------------------------------------------------

class TestContextSnippet:
    def test_first_call_returns_empty_snippet(self):
        m = make_memory()
        assert m.get(PHONE).context_snippet() == ""

    def test_returning_lead_snippet_is_non_empty(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        snippet = m.get(PHONE).context_snippet()
        assert isinstance(snippet, str)
        assert len(snippet) > 0

    def test_snippet_mentions_status(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        snippet = m.get(PHONE).context_snippet().lower()
        assert "interest" in snippet or "hot" in snippet or "called" in snippet

    def test_snippet_mentions_price_objection(self):
        m = make_memory()
        m.record(PHONE, "c1", "WARM", 0.6, primary_objection="price")
        snippet = m.get(PHONE).context_snippet().lower()
        assert "price" in snippet or "budget" in snippet or "concern" in snippet


# ---------------------------------------------------------------------------
# Priya opener
# ---------------------------------------------------------------------------

class TestPriyaOpener:
    def test_first_call_opener_is_empty(self):
        m = make_memory()
        assert m.get(PHONE).priya_opener() == ""

    def test_hot_returning_has_opener(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        opener = m.get(PHONE).priya_opener()
        assert len(opener) > 10

    def test_price_returning_mentions_budget(self):
        m = make_memory()
        m.record(PHONE, "c1", "WARM", 0.6, primary_objection="price")
        opener = m.get(PHONE).priya_opener().lower()
        assert "budget" in opener or "₹" in opener or "price" in opener

    def test_cold_returning_has_opener(self):
        m = make_memory()
        m.record(PHONE, "c1", "COLD", 0.8)
        opener = m.get(PHONE).priya_opener()
        assert isinstance(opener, str)
        assert len(opener) > 0


# ---------------------------------------------------------------------------
# as_dict
# ---------------------------------------------------------------------------

class TestAsDict:
    def test_as_dict_has_required_keys(self):
        m = make_memory()
        d = m.get(PHONE).as_dict()
        for k in ('phone', 'call_count', 'is_returning', 'last_status',
                  'context_snippet', 'priya_opener', 'history'):
            assert k in d

    def test_as_dict_json_serializable(self):
        import json
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9, primary_objection="price",
                 persona="Executive", arc="cold→hot", momentum=0.7, quality_score=82)
        d = m.get(PHONE).as_dict()
        json.dumps(d)  # should not raise


# ---------------------------------------------------------------------------
# all_profiles / len
# ---------------------------------------------------------------------------

class TestAllProfiles:
    def test_empty_memory_has_len_0(self):
        m = make_memory()
        assert len(m) == 0

    def test_after_record_len_is_1(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        assert len(m) == 1

    def test_two_phones_len_2(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        m.record(PHONE2, "c2", "COLD", 0.8)
        assert len(m) == 2

    def test_all_profiles_returns_list(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        profiles = m.all_profiles()
        assert isinstance(profiles, list)
        assert len(profiles) >= 1

    def test_clear_resets_store(self):
        m = make_memory()
        m.record(PHONE, "c1", "HOT", 0.9)
        m.clear()
        assert len(m) == 0

    def test_singleton_persists_across_imports(self):
        from src.memory import lead_memory
        lead_memory.record(PHONE, "x1", "HOT", 0.9)
        from src.memory import lead_memory as lm2
        assert lm2.get(PHONE).call_count >= 1


# ---------------------------------------------------------------------------
# Redis fallback
# ---------------------------------------------------------------------------

class TestRedisFallback:
    def test_bad_redis_url_falls_back_to_memory(self):
        m = LeadMemory(redis_url="redis://localhost:1")  # no Redis running
        m.record(PHONE, "c1", "HOT", 0.9)
        assert m.get(PHONE).call_count == 1
