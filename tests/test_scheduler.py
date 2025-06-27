"""
Tests for natural language time resolution in the scheduler.

These tests are pure unit tests — no APScheduler, no Vapi, no network.
The reference_now parameter freezes time so results are deterministic.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.scheduler import resolve_callback_time

IST = ZoneInfo("Asia/Kolkata")


def _now(hour=14, minute=0, day=22, month=8, year=2026) -> datetime:
    """Build a reference datetime in IST for use in tests."""
    return datetime(year, month, day, hour, minute, 0, tzinfo=IST)


class TestEnglishTimeResolution:
    """English language callback time phrases."""

    def test_tomorrow_morning(self):
        result = resolve_callback_time("call me tomorrow morning", reference_now=_now())
        assert result is not None
        assert result.day == 23          # next day
        assert result.hour == 9          # morning = 9 AM

    def test_tomorrow_afternoon(self):
        result = resolve_callback_time("tomorrow afternoon", reference_now=_now())
        assert result is not None
        assert result.day == 23
        assert result.hour == 14

    def test_tomorrow_evening(self):
        result = resolve_callback_time("call me tomorrow evening", reference_now=_now())
        assert result is not None
        assert result.day == 23
        assert result.hour == 18

    def test_explicit_time(self):
        result = resolve_callback_time("call me at 3 PM", reference_now=_now(hour=10))
        assert result is not None
        assert result.hour == 15
        assert result.minute == 0

    def test_explicit_time_with_minutes(self):
        result = resolve_callback_time("after 5:30 PM", reference_now=_now(hour=10))
        assert result is not None
        assert result.hour == 17
        assert result.minute == 30

    def test_friday_morning(self):
        # Reference is Saturday Aug 22 2026, Friday = next Friday Aug 28
        result = resolve_callback_time("Friday morning", reference_now=_now())
        assert result is not None
        assert result.weekday() == 4     # Friday
        assert result.hour == 9

    def test_past_time_pushes_to_next_day(self):
        """If the resolved time has already passed today, bump to tomorrow."""
        # It's 2 PM, asking for "at 10 AM" → should be tomorrow at 10 AM
        result = resolve_callback_time("at 10 AM", reference_now=_now(hour=14))
        assert result is not None
        assert result.day == 23

    def test_returns_ist_timezone(self):
        result = resolve_callback_time("tomorrow morning", reference_now=_now())
        assert result is not None
        assert result.tzinfo is not None
        assert "Asia" in str(result.tzinfo) or "Kolkata" in str(result.tzinfo)


class TestHindiTimeResolution:
    """Hindi language callback time phrases."""

    def test_kal_subah(self):
        result = resolve_callback_time("kal subah call karna", reference_now=_now())
        assert result is not None
        assert result.day == 23
        assert result.hour == 9

    def test_kal_shaam(self):
        result = resolve_callback_time("kal shaam baat karte hain", reference_now=_now())
        assert result is not None
        assert result.day == 23
        assert result.hour == 18

    def test_kal_dopahar(self):
        result = resolve_callback_time("kal dopahar mein call karo", reference_now=_now())
        assert result is not None
        assert result.day == 23
        assert result.hour == 14

    def test_parso(self):
        """parso = day after tomorrow in Hindi."""
        result = resolve_callback_time("parso baat karte hain", reference_now=_now())
        assert result is not None
        assert result.day == 24    # 2 days from Aug 22


class TestTeluguTimeResolution:
    """Telugu language callback time phrases."""

    def test_kal_udayam(self):
        result = resolve_callback_time("kal udayam call cheyyandi", reference_now=_now())
        assert result is not None
        assert result.day == 23
        assert result.hour == 9

    def test_kal_saayantram(self):
        result = resolve_callback_time("kal saayantram matladdam", reference_now=_now())
        assert result is not None
        assert result.day == 23
        assert result.hour == 18


class TestFallbackBehaviour:
    """Fallback when the phrase is vague or unrecognised."""

    def test_vague_phrase_returns_something(self):
        """Should not return None — always give a sensible default."""
        result = resolve_callback_time("some other time maybe", reference_now=_now())
        assert result is not None

    def test_empty_string_returns_something(self):
        result = resolve_callback_time("", reference_now=_now())
        assert result is not None

    def test_result_is_always_in_future(self):
        """Every resolved time must be strictly in the future."""
        now = _now()
        result = resolve_callback_time("tomorrow morning", reference_now=now)
        assert result > now
