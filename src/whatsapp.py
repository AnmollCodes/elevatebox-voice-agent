"""
WhatsApp messaging via Twilio.

Two paths:
  1. Mid-call HOT message — fires while the call is still live.
     Must be fast. No blocking I/O on the webhook response path.
  2. Post-call summary — fires after end-of-call-report webhook.
     Can take a moment since the call is already over.

Both paths use the Twilio Messages API. The recipient (8688664337) must have
joined the Twilio Sandbox ("join <word>") or the account must be on a
production WhatsApp sender. Instructions in README.
"""

import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class WhatsAppSender:
    """
    Wraps Twilio's Messages API for WhatsApp delivery.
    Initialised once at startup; stateless per call.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,          # "whatsapp:+14155238886"
        candidate_mobile: str,
        candidate_resume_url: str,
        architecture_image_url: str,
    ) -> None:
        """
        Args:
            account_sid: Twilio account SID.
            auth_token: Twilio auth token.
            from_number: Twilio WhatsApp sender in "whatsapp:+1..." format.
            candidate_mobile: The builder's phone number shown in messages.
            candidate_resume_url: Publicly accessible URL for the resume PDF.
            architecture_image_url: Publicly accessible URL for arch diagram.
        """
        self._account_sid = account_sid
        self._auth_token = auth_token
        self._from_number = from_number
        self.candidate_mobile = candidate_mobile
        self.candidate_resume_url = candidate_resume_url
        self.architecture_image_url = architecture_image_url
        self._client = None

    def _get_client(self):
        """
        Lazy-init Twilio client to avoid import cost at module load time.
        Keeps the import inside the method so tests can mock it cleanly.
        """
        if self._client is None:
            from twilio.rest import Client as TwilioClient  # noqa: PLC0415
            self._client = TwilioClient(self._account_sid, self._auth_token)
        return self._client

    async def send_hot_lead_message(
        self,
        to: str,
        call_context: str,
        budget: Optional[str] = None,
        timeline: Optional[str] = None,
    ) -> bool:
        """
        Send the mid-call WhatsApp message to a HOT lead.

        Runs in a thread pool to avoid blocking the async event loop while
        Twilio's synchronous SDK makes its HTTP request.

        Args:
            to: Recipient in "whatsapp:+91..." format.
            call_context: Human-readable summary of the call so far.
            budget: Budget figure mentioned by the customer, if any.
            timeline: Timeline mentioned by the customer, if any.

        Returns:
            True if the message was queued by Twilio, False on error.
        """
        body = self._build_hot_message(call_context, budget, timeline)
        return await self._send(to=to, body=body)

    async def send_post_call_summary(
        self,
        to: str,
        call_context: str,
        lead_status: str,
        barrier: Optional[str] = None,
        callback_time: Optional[str] = None,
    ) -> bool:
        """
        Send the comprehensive post-call WhatsApp summary.

        Must include: call context, candidate mobile, architecture image.
        Resume is attached as a media URL when available.

        Args:
            to: Recipient in "whatsapp:+91..." format.
            call_context: Human-written summary of what was discussed.
            lead_status: HOT / WARM / COLD.
            barrier: For WARM leads, what is blocking them.
            callback_time: For WARM leads, when to call back.

        Returns:
            True if the message was queued by Twilio, False on error.
        """
        body = self._build_post_call_message(call_context, lead_status, barrier, callback_time)
        media_urls = [url for url in [self.candidate_resume_url, self.architecture_image_url] if url]
        return await self._send(to=to, body=body, media_urls=media_urls or None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_hot_message(
        self,
        call_context: str,
        budget: Optional[str],
        timeline: Optional[str],
    ) -> str:
        """Build the mid-call HOT lead WhatsApp message body."""
        extras = []
        if budget:
            extras.append(f"💰 Budget you mentioned: {budget}")
        if timeline:
            extras.append(f"📅 Timeline: {timeline}")
        extras_block = "\n".join(extras) + "\n\n" if extras else ""

        return (
            f"Hey! 👋 This is Priya from ElevateBox — we're speaking right now.\n\n"
            f"Quick note while we chat:\n\n"
            f"{call_context}\n\n"
            f"{extras_block}"
            f"I'll send a full summary once we hang up. In the meantime:\n"
            f"📞 Reach me directly: {self.candidate_mobile or 'see full summary'}\n\n"
            f"— ElevateBox, Banjara Hills, Hyderabad"
        )

    def _build_post_call_message(
        self,
        call_context: str,
        lead_status: str,
        barrier: Optional[str],
        callback_time: Optional[str],
    ) -> str:
        """Build the post-call WhatsApp message body based on lead status."""
        status_note = ""
        if lead_status == "WARM" and barrier:
            status_note = (
                f"I understand {barrier} — absolutely no pressure. "
                f"We'll be in touch when the time is right.\n\n"
            )
        if lead_status == "WARM" and callback_time:
            status_note += f"📆 Callback booked: {callback_time}\n\n"

        arch_line = (
            f"\n🏗 How I built the system that just called you:\n{self.architecture_image_url}"
            if self.architecture_image_url
            else ""
        )

        return (
            f"Hey! 👋 Priya from ElevateBox here.\n\n"
            f"Thanks for the chat — here's a quick recap:\n\n"
            f"{call_context}\n\n"
            f"{status_note}"
            f"📞 My direct number: {self.candidate_mobile or '[see attached resume]'}\n"
            f"{arch_line}\n\n"
            f"Looking forward to building something great with you!\n\n"
            f"— ElevateBox | Banjara Hills, Hyderabad"
        )

    async def _send(
        self,
        to: str,
        body: str,
        media_urls: Optional[list[str]] = None,
    ) -> bool:
        """
        Fire the Twilio API call in a thread pool so it does not block the
        async event loop.

        Args:
            to: Recipient number in "whatsapp:+..." format.
            body: Message text.
            media_urls: Optional list of publicly accessible media URLs.

        Returns:
            True on success, False on any error.
        """
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self._send_sync(to=to, body=body, media_urls=media_urls),
            )
            return result
        except Exception:
            # Broad catch because Twilio can throw various network/HTTP errors.
            # We log the error type but never the message body (could contain PII).
            logger.exception("WhatsApp send failed (recipient masked)")
            return False

    def _send_sync(
        self,
        to: str,
        body: str,
        media_urls: Optional[list[str]] = None,
    ) -> bool:
        """
        Synchronous Twilio API call. Run this only inside run_in_executor.

        Returns:
            True if Twilio accepted the message (SID returned), False otherwise.
        """
        client = self._get_client()
        kwargs: dict = {
            "from_": self._from_number,
            "to": to,
            "body": body,
        }
        if media_urls:
            kwargs["media_url"] = media_urls

        msg = client.messages.create(**kwargs)
        # Log only the SID, never the body — it may contain customer details
        logger.info("WhatsApp message queued, SID=%s, status=%s", msg.sid, msg.status)
        return bool(msg.sid)
