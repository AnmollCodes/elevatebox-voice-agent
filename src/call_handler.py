"""
Vapi webhook event handler.

Vapi sends POST requests to /webhook/vapi for every event during a call:
  - assistant-request      : Vapi asking which assistant to use (we use inline config, so rarely needed)
  - function-call          : LLM wants to execute one of our tools
  - end-of-call-report     : Call ended, full transcript available
  - status-update          : Call state changes (ringing, in-progress, ended)

This module processes each event type and returns the correct Vapi response format.
The response MUST be fast — Vapi will time out if we block for more than a few seconds.

WhatsApp sends and scheduling are done async so they do not block the response.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .classifier import LeadProfile, LeadStatus, classify_from_transcript, extract_budget_from_transcript
from .config import Config
from .scheduler import resolve_callback_time, schedule_callback
from .whatsapp import WhatsAppSender

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")


class CallHandler:
    """
    Processes Vapi webhook events for a single server instance.
    Holds references to shared services (WhatsApp sender, config).
    """

    def __init__(self, config: Config, whatsapp: WhatsAppSender) -> None:
        self._config = config
        self._whatsapp = whatsapp
        # In-memory call state: call_id → LeadProfile
        # For a POC this is fine. Production would use Redis.
        self._call_state: dict[str, LeadProfile] = {}

    async def handle_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Route a Vapi webhook payload to the correct handler.

        Args:
            payload: Parsed JSON body from Vapi.

        Returns:
            Dict that becomes the JSON response body to Vapi.
        """
        message = payload.get("message", {})
        event_type = message.get("type", "")
        call_id = message.get("call", {}).get("id", "unknown")

        logger.info("Vapi event: type=%s, call_id=%s", event_type, call_id)

        if event_type == "function-call":
            return await self._handle_function_call(message, call_id)
        elif event_type == "end-of-call-report":
            return await self._handle_end_of_call(message, call_id)
        elif event_type == "status-update":
            return await self._handle_status_update(message, call_id)
        else:
            # Unknown or informational events — acknowledge and move on
            logger.debug("Unhandled Vapi event type: %s", event_type)
            return {"result": "acknowledged"}

    # ------------------------------------------------------------------
    # Function call handler — fires when the LLM calls one of our tools
    # ------------------------------------------------------------------

    async def _handle_function_call(self, message: dict, call_id: str) -> dict[str, Any]:
        """
        Handle a tool call from the voice agent LLM.

        Vapi expects a response with {"result": "..."} where result is
        the string that gets passed back to the LLM as the function return value.

        Args:
            message: The "message" object from the webhook payload.
            call_id: Vapi call ID.

        Returns:
            {"result": "..."} dict.
        """
        fn_call = message.get("functionCall", {})
        fn_name = fn_call.get("name", "")
        fn_args = fn_call.get("parameters", {})

        logger.info("Function call: %s, call_id=%s", fn_name, call_id)

        if fn_name == "send_whatsapp_hot_lead":
            return await self._fn_send_whatsapp_hot(fn_args, call_id)
        elif fn_name == "book_callback":
            return await self._fn_book_callback(fn_args, call_id)
        elif fn_name == "end_call_summary":
            return await self._fn_end_call_summary(fn_args, call_id)
        else:
            logger.warning("Unknown function called: %s", fn_name)
            return {"result": f"Function '{fn_name}' is not implemented."}

    async def _fn_send_whatsapp_hot(self, args: dict, call_id: str) -> dict[str, Any]:
        """
        Handle send_whatsapp_hot_lead function call.

        Fires the WhatsApp in the background so the Vapi response is instant
        and the conversation is not blocked.

        Args:
            args: Function parameters from the LLM.
            call_id: Vapi call ID.

        Returns:
            Vapi function result string in {"result": "..."} format.
        """
        call_context = args.get("call_context", "")
        budget = args.get("budget_mentioned")
        timeline = args.get("timeline")

        # Mark the call as HOT in local state
        profile = self._get_or_create_profile(call_id)
        profile.status = LeadStatus.HOT
        profile.budget = budget
        profile.timeline = timeline
        profile.mid_call_whatsapp_sent = True

        # Fire WhatsApp without waiting — response must be fast
        asyncio.create_task(
            self._whatsapp.send_hot_lead_message(
                to=self._config.target_whatsapp,
                call_context=call_context,
                budget=budget,
                timeline=timeline,
            )
        )

        logger.info("HOT lead WhatsApp queued, call_id=%s", call_id)
        return {
            "result": (
                "WhatsApp sent successfully. "
                "Tell the customer: 'I've just sent you a WhatsApp with our details — "
                "you should see it in the next few seconds.'"
            )
        }

    async def _fn_book_callback(self, args: dict, call_id: str) -> dict[str, Any]:
        """
        Handle book_callback function call.

        Resolves the time phrase and schedules a follow-up call.

        Args:
            args: Function parameters from the LLM.
            call_id: Vapi call ID.

        Returns:
            Vapi function result string confirming the booking.
        """
        raw_phrase = args.get("raw_time_phrase", "")
        resolved_str = args.get("resolved_datetime", "")
        barrier = args.get("customer_barrier", "")

        profile = self._get_or_create_profile(call_id)
        profile.status = LeadStatus.WARM
        profile.barrier = barrier
        profile.callback_time = raw_phrase
        profile.callback_time_resolved = resolved_str

        # Try to resolve the time for actual scheduling
        resolved_dt: Optional[datetime] = resolve_callback_time(raw_phrase)

        if resolved_dt:
            # We schedule an async job — for a real system this would place
            # another Vapi call. For now it logs the intent.
            try:
                from .vapi_client import VapiClient  # local import to avoid circular dep
                job_id = schedule_callback(
                    phone_number=self._config.target_phone,
                    callback_time=resolved_dt,
                    call_context=f"Callback requested. Barrier: {barrier}. Raw phrase: {raw_phrase}",
                    callback_fn=_noop_callback,  # replace with actual call trigger in prod
                )
                logger.info("Callback scheduled, job_id=%s, time=%s", job_id, resolved_dt.isoformat())
                time_display = resolved_dt.strftime("%-d %B at %-I:%M %p IST")
            except Exception:
                logger.exception("Failed to schedule callback for call_id=%s", call_id)
                time_display = resolved_str or raw_phrase
        else:
            time_display = resolved_str or raw_phrase

        return {
            "result": (
                f"Callback booked for {time_display}. "
                f"Tell the customer: 'Perfect, I've noted that down. "
                f"Someone from our team will call you {raw_phrase}.'"
            )
        }

    async def _fn_end_call_summary(self, args: dict, call_id: str) -> dict[str, Any]:
        """
        Handle end_call_summary function call.

        Stores the final classification and triggers the post-call WhatsApp.

        Args:
            args: Function parameters from the LLM.
            call_id: Vapi call ID.

        Returns:
            Vapi function result acknowledging receipt.
        """
        lead_status = args.get("lead_status", "WARM")
        call_context = args.get("call_context", "")
        customer_name = args.get("customer_name")

        profile = self._get_or_create_profile(call_id)
        if profile.status == LeadStatus.UNKNOWN:
            profile.status = LeadStatus(lead_status)
        profile.customer_name = customer_name

        # Queue the post-call WhatsApp (only if not already sent as HOT mid-call)
        asyncio.create_task(
            self._send_post_call_whatsapp(profile, call_context)
        )

        return {"result": "Summary received. Post-call WhatsApp will be sent shortly."}

    # ------------------------------------------------------------------
    # End-of-call-report handler
    # ------------------------------------------------------------------

    async def _handle_end_of_call(self, message: dict, call_id: str) -> dict[str, Any]:
        """
        Handle the end-of-call-report event from Vapi.

        This fires after the call ends and contains the full transcript.
        We use it as a safety net: if end_call_summary function was not called
        during the call (e.g., unexpected hang-up), we still send the summary.

        Args:
            message: Vapi message object.
            call_id: Vapi call ID.

        Returns:
            Acknowledgement dict.
        """
        summary = message.get("summary", "")
        transcript = message.get("transcript", "")
        duration = int(message.get("durationSeconds", 0))

        profile = self._get_or_create_profile(call_id)
        profile.call_duration_seconds = duration
        profile.transcript = transcript

        # Fallback classification from transcript if the LLM didn't classify
        if profile.status == LeadStatus.UNKNOWN:
            profile.status = classify_from_transcript(transcript or summary)
            logger.info(
                "Fallback classification for call_id=%s: %s",
                call_id, profile.status
            )

        if not profile.mid_call_whatsapp_sent:
            # Build context from summary + transcript budget extraction
            if not profile.budget:
                profile.budget = extract_budget_from_transcript(transcript)
            context = summary or transcript[:500] or "No detailed context captured."
            asyncio.create_task(
                self._send_post_call_whatsapp(profile, context)
            )

        logger.info(
            "Call ended: call_id=%s, status=%s, duration=%ds",
            call_id, profile.status, duration,
        )
        return {"result": "end-of-call processed"}

    async def _handle_status_update(self, message: dict, call_id: str) -> dict[str, Any]:
        """
        Log call status changes. No action needed for most states.

        Args:
            message: Vapi message object.
            call_id: Vapi call ID.

        Returns:
            Acknowledgement dict.
        """
        status = message.get("status", "")
        logger.info("Call status update: call_id=%s, status=%s", call_id, status)
        return {"result": "ok"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_or_create_profile(self, call_id: str) -> LeadProfile:
        """
        Return existing call profile or create a fresh one.

        Args:
            call_id: Vapi call ID used as dict key.

        Returns:
            LeadProfile for this call.
        """
        if call_id not in self._call_state:
            self._call_state[call_id] = LeadProfile()
        return self._call_state[call_id]

    async def _send_post_call_whatsapp(self, profile: LeadProfile, call_context: str) -> None:
        """
        Fire the post-call WhatsApp summary.

        Args:
            profile: Lead profile with classification and call details.
            call_context: Human-readable summary of the conversation.
        """
        sent = await self._whatsapp.send_post_call_summary(
            to=self._config.target_whatsapp,
            call_context=call_context,
            lead_status=profile.status.value,
            barrier=profile.barrier,
            callback_time=profile.callback_time_resolved or profile.callback_time,
        )
        if sent:
            logger.info("Post-call WhatsApp sent, status=%s", profile.status)
        else:
            logger.error("Post-call WhatsApp FAILED, status=%s", profile.status)


# ---------------------------------------------------------------------------
# Placeholder callback function for scheduled follow-up calls.
# In production, replace with actual Vapi outbound call trigger.
# ---------------------------------------------------------------------------

async def _noop_callback(phone_number: str, context: str) -> None:
    """
    Placeholder that logs the callback trigger.
    Replace with: await vapi_client.place_call(phone_number) in production.

    Args:
        phone_number: Number to call back.
        context: Call context string.
    """
    logger.info(
        "Callback triggered for %s — context: %s",
        phone_number[:6] + "****",  # mask most of the number in logs
        context[:100],
    )
