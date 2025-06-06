"""
Tests for WhatsApp message building.

These tests verify the message content and structure without making
any real Twilio API calls.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.whatsapp import WhatsAppSender


@pytest.fixture()
def sender():
    return WhatsAppSender(
        account_sid="test_sid",
        auth_token="test_token",
        from_number="whatsapp:+14155238886",
        candidate_mobile="+919876543210",
        candidate_resume_url="https://example.com/resume.pdf",
        architecture_image_url="https://example.com/arch.png",
    )


class TestHotMessageBuilding:
    """Verify HOT lead message content without sending."""

    def test_hot_message_includes_candidate_mobile(self, sender):
        msg = sender._build_hot_message(
            call_context="Customer wants a fashion e-commerce store.",
            budget="₹75,000",
            timeline="6 weeks",
        )
        assert "+919876543210" in msg

    def test_hot_message_includes_budget(self, sender):
        msg = sender._build_hot_message(
            call_context="Customer sells organic food online.",
            budget="₹40,000",
            timeline="4 weeks",
        )
        assert "₹40,000" in msg

    def test_hot_message_includes_timeline(self, sender):
        msg = sender._build_hot_message(
            call_context="Electronics retailer.",
            budget=None,
            timeline="2 months",
        )
        assert "2 months" in msg

    def test_hot_message_omits_budget_block_when_none(self, sender):
        msg = sender._build_hot_message(
            call_context="Customer wants to sell sarees.",
            budget=None,
            timeline=None,
        )
        # Should not have empty budget line
        assert "Budget you mentioned:" not in msg

    def test_hot_message_has_elevatebox_signature(self, sender):
        msg = sender._build_hot_message("context", None, None)
        assert "ElevateBox" in msg


class TestPostCallMessageBuilding:
    """Verify post-call message content."""

    def test_hot_post_call_message_has_mobile(self, sender):
        msg = sender._build_post_call_message(
            call_context="Wanted full-featured store with payment gateway.",
            lead_status="HOT",
            barrier=None,
            callback_time=None,
        )
        assert "+919876543210" in msg

    def test_warm_message_includes_barrier(self, sender):
        msg = sender._build_post_call_message(
            call_context="Customer interested but needs partner approval.",
            lead_status="WARM",
            barrier="needs approval from partner",
            callback_time="tomorrow at 10 AM IST",
        )
        assert "needs approval from partner" in msg

    def test_warm_message_includes_callback_time(self, sender):
        msg = sender._build_post_call_message(
            call_context="Will call back when ready.",
            lead_status="WARM",
            barrier=None,
            callback_time="Friday at 3 PM IST",
        )
        assert "Friday at 3 PM IST" in msg

    def test_cold_message_is_gracious(self, sender):
        msg = sender._build_post_call_message(
            call_context="Not interested at this time.",
            lead_status="COLD",
            barrier=None,
            callback_time=None,
        )
        assert "ElevateBox" in msg

    def test_architecture_image_url_in_message(self, sender):
        msg = sender._build_post_call_message(
            call_context="Good conversation.",
            lead_status="HOT",
            barrier=None,
            callback_time=None,
        )
        assert "https://example.com/arch.png" in msg


class TestSenderConfiguration:
    """Test that missing Twilio config is handled gracefully."""

    def test_send_returns_false_when_twilio_raises(self):
        """If Twilio's SDK raises, _send should catch it and return False."""
        import asyncio

        sender = WhatsAppSender(
            account_sid="bad_sid",
            auth_token="bad_token",
            from_number="whatsapp:+14155238886",
            candidate_mobile="+919876543210",
            candidate_resume_url="",
            architecture_image_url="",
        )

        with patch.object(sender, "_send_sync", side_effect=Exception("Twilio auth error")):
            result = asyncio.get_event_loop().run_until_complete(
                sender._send(to="whatsapp:+918688664337", body="test message")
            )
        assert result is False
