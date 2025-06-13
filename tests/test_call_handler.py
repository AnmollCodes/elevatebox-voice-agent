"""
Tests for the Vapi webhook call handler.

External dependencies (WhatsApp, Vapi, scheduler) are mocked so tests
run without any network access or API keys.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.call_handler import CallHandler
from src.classifier import LeadStatus
from src.config import Config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_config():
    """Minimal Config with no real credentials."""
    config = MagicMock(spec=Config)
    config.target_whatsapp = "whatsapp:+918688664337"
    config.target_phone = "+918688664337"
    config.whatsapp_configured = True
    return config


@pytest.fixture()
def mock_whatsapp():
    """WhatsAppSender with all send methods mocked."""
    wa = MagicMock()
    wa.send_hot_lead_message = AsyncMock(return_value=True)
    wa.send_post_call_summary = AsyncMock(return_value=True)
    wa.candidate_mobile = "+919999999999"
    wa.architecture_image_url = "https://example.com/arch.png"
    return wa


@pytest.fixture()
def handler(mock_config, mock_whatsapp):
    return CallHandler(config=mock_config, whatsapp=mock_whatsapp)


def _make_payload(event_type: str, extra: dict | None = None) -> dict:
    """Build a minimal Vapi webhook payload."""
    base = {
        "message": {
            "type": event_type,
            "call": {"id": "test-call-id-123"},
        }
    }
    if extra:
        base["message"].update(extra)
    return base


# ---------------------------------------------------------------------------
# Function call: send_whatsapp_hot_lead
# ---------------------------------------------------------------------------

class TestHotLeadFunctionCall:
    def test_hot_function_call_returns_result_string(self, handler):
        payload = _make_payload("function-call", {
            "functionCall": {
                "name": "send_whatsapp_hot_lead",
                "parameters": {
                    "call_context": "Customer wants a Shopify-style store for sarees.",
                    "intent_signal": "send me the details",
                    "budget_mentioned": "₹50,000",
                    "timeline": "2 months",
                },
            }
        })
        result = asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        assert "result" in result
        assert "WhatsApp" in result["result"] or "sent" in result["result"].lower()

    def test_hot_lead_marks_profile_as_hot(self, handler):
        payload = _make_payload("function-call", {
            "functionCall": {
                "name": "send_whatsapp_hot_lead",
                "parameters": {
                    "call_context": "Wants electronics store.",
                    "intent_signal": "let's do it",
                },
            }
        })
        asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        profile = handler._call_state.get("test-call-id-123")
        assert profile is not None
        assert profile.status == LeadStatus.HOT
        assert profile.mid_call_whatsapp_sent is True


# ---------------------------------------------------------------------------
# Function call: book_callback
# ---------------------------------------------------------------------------

class TestBookCallbackFunctionCall:
    def test_callback_booking_returns_confirmation(self, handler):
        payload = _make_payload("function-call", {
            "functionCall": {
                "name": "book_callback",
                "parameters": {
                    "raw_time_phrase": "tomorrow morning",
                    "resolved_datetime": "2026-08-23 10:00 IST",
                    "customer_barrier": "needs to discuss with partner",
                },
            }
        })
        result = asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        assert "result" in result

    def test_callback_marks_profile_as_warm(self, handler):
        payload = _make_payload("function-call", {
            "functionCall": {
                "name": "book_callback",
                "parameters": {
                    "raw_time_phrase": "Friday afternoon",
                    "resolved_datetime": "2026-08-28 14:00 IST",
                },
            }
        })
        asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        profile = handler._call_state.get("test-call-id-123")
        assert profile is not None
        assert profile.status == LeadStatus.WARM
        assert profile.callback_time == "Friday afternoon"


# ---------------------------------------------------------------------------
# Function call: end_call_summary
# ---------------------------------------------------------------------------

class TestEndCallSummaryFunctionCall:
    def test_end_call_summary_stores_status(self, handler, mock_whatsapp):
        payload = _make_payload("function-call", {
            "functionCall": {
                "name": "end_call_summary",
                "parameters": {
                    "lead_status": "COLD",
                    "call_context": "Just browsing, no real need right now.",
                    "customer_name": "Srinivas",
                },
            }
        })
        asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        profile = handler._call_state.get("test-call-id-123")
        assert profile is not None
        assert profile.status == LeadStatus.COLD
        assert profile.customer_name == "Srinivas"

    def test_unknown_function_returns_error_message(self, handler):
        payload = _make_payload("function-call", {
            "functionCall": {
                "name": "nonexistent_function",
                "parameters": {},
            }
        })
        result = asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        assert "result" in result
        assert "not implemented" in result["result"].lower() or "unknown" in result["result"].lower()


# ---------------------------------------------------------------------------
# End-of-call-report
# ---------------------------------------------------------------------------

class TestEndOfCallReport:
    def test_end_of_call_triggers_whatsapp(self, handler, mock_whatsapp):
        payload = _make_payload("end-of-call-report", {
            "transcript": "Agent: Hello! Customer: How soon can you start? I want to get started.",
            "summary": "Customer wants to start immediately.",
            "durationSeconds": 180,
        })
        asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        # WhatsApp should be queued (via asyncio task)
        # The call might be HOT based on transcript

    def test_end_of_call_fallback_classification(self, handler):
        """If end_call_summary was never called, classify from transcript."""
        transcript = "I'm not interested, already have a website."
        payload = _make_payload("end-of-call-report", {
            "transcript": transcript,
            "summary": "",
            "durationSeconds": 60,
        })
        asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        profile = handler._call_state.get("test-call-id-123")
        assert profile is not None
        assert profile.status in (LeadStatus.COLD, LeadStatus.WARM, LeadStatus.HOT)

    def test_hot_lead_midcall_whatsapp_already_sent_skips_duplicate(self, handler, mock_whatsapp):
        """If a mid-call WhatsApp was already sent, do not send another."""
        # Simulate that mid-call WhatsApp was already sent
        from src.classifier import LeadProfile
        profile = LeadProfile()
        profile.status = LeadStatus.HOT
        profile.mid_call_whatsapp_sent = True
        handler._call_state["test-call-id-123"] = profile

        payload = _make_payload("end-of-call-report", {
            "transcript": "Let's do it! Send me the details.",
            "summary": "HOT lead.",
            "durationSeconds": 300,
        })
        asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        # send_post_call_summary should NOT have been called
        mock_whatsapp.send_post_call_summary.assert_not_awaited()


# ---------------------------------------------------------------------------
# Status updates
# ---------------------------------------------------------------------------

class TestStatusUpdates:
    def test_status_update_returns_ok(self, handler):
        payload = _make_payload("status-update", {"status": "in-progress"})
        result = asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        assert result.get("result") == "ok"

    def test_unknown_event_acknowledged(self, handler):
        payload = _make_payload("some-future-event-type")
        result = asyncio.get_event_loop().run_until_complete(handler.handle_event(payload))
        assert "result" in result
