"""
Vapi.ai API client.

Handles:
  - Creating / retrieving the voice assistant configuration
  - Placing outbound calls
  - Defining the tools (functions) the assistant can call mid-conversation

Vapi docs: https://docs.vapi.ai
"""

import logging
from typing import Any, Optional

import httpx

from .config import Config
from .prompts import AGENT_SYSTEM_PROMPT, AGENT_FIRST_MESSAGE

logger = logging.getLogger(__name__)

VAPI_BASE_URL = "https://api.vapi.ai"


class VapiClient:
    """
    Thin wrapper around the Vapi REST API.
    Uses httpx with a shared async client for connection reuse.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._headers = {
            "Authorization": f"Bearer {config.vapi_api_key}",
            "Content-Type": "application/json",
        }
        # httpx async client shared across all calls
        self._http: Optional[httpx.AsyncClient] = None

    async def _client(self) -> httpx.AsyncClient:
        """Return the shared async HTTP client, creating it if needed."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=VAPI_BASE_URL,
                headers=self._headers,
                timeout=30.0,
            )
        return self._http

    async def close(self) -> None:
        """Release the HTTP connection pool. Call at app shutdown."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            logger.info("VapiClient HTTP client closed")

    # ------------------------------------------------------------------
    # Outbound call
    # ------------------------------------------------------------------

    async def place_call(self, phone_number: Optional[str] = None) -> dict[str, Any]:
        """
        Place an outbound call using the configured Vapi phone number.

        The assistant configuration is embedded inline — no pre-created
        assistant ID needed, which keeps setup simpler.

        Args:
            phone_number: E.164 number to call. Defaults to target from config.

        Returns:
            Vapi call object dict with at least {"id": "...", "status": "..."}.

        Raises:
            httpx.HTTPStatusError: If Vapi returns a non-2xx response.
        """
        target = phone_number or self._config.target_phone
        payload = self._build_call_payload(target)

        client = await self._client()
        response = await client.post("/call/phone", json=payload)

        if response.status_code not in (200, 201):
            # Log status and Vapi's error message but not the full payload
            # (it contains the API key reference and system prompt)
            logger.error(
                "Vapi call creation failed: HTTP %d — %s",
                response.status_code,
                response.text[:300],
            )
            response.raise_for_status()

        call_data = response.json()
        logger.info("Outbound call placed, call_id=%s, status=%s", call_data.get("id"), call_data.get("status"))
        return call_data

    async def get_call(self, call_id: str) -> dict[str, Any]:
        """
        Fetch the current state of a call.

        Args:
            call_id: Vapi call ID from place_call response.

        Returns:
            Vapi call object.
        """
        client = await self._client()
        response = await client.get(f"/call/{call_id}")
        response.raise_for_status()
        return response.json()

    # ------------------------------------------------------------------
    # Payload construction
    # ------------------------------------------------------------------

    def _build_call_payload(self, phone_number: str) -> dict[str, Any]:
        """
        Build the full Vapi call creation payload.

        The assistant is defined inline (not pre-registered) so there is
        one less resource to manage. The model, voice, and tools are all
        configured here.

        Args:
            phone_number: E.164 number to call.

        Returns:
            Dict ready to JSON-serialise and POST to /call/phone.
        """
        return {
            "phoneNumberId": self._config.vapi_phone_number_id,
            "customer": {
                "number": phone_number,
            },
            "assistant": {
                "name": "Priya - ElevateBox Sales",
                "model": {
                    "provider": "openai",
                    "model": self._config.openai_model,
                    "systemPrompt": AGENT_SYSTEM_PROMPT,
                    "temperature": 0.7,
                    "tools": self._build_tools(),
                },
                "voice": {
                    # Shimmer is a natural-sounding female voice.
                    # A non-studio voice reduces hang-ups on outbound.
                    "provider": "openai",
                    "voiceId": "shimmer",
                    "stability": 0.5,
                    "similarityBoost": 0.8,
                },
                "firstMessage": AGENT_FIRST_MESSAGE,
                "firstMessageMode": "assistant-speaks-first",
                "endCallMessage": "Thank you for your time! Have a great day.",
                "endCallPhrases": [
                    "goodbye", "bye", "take care", "alvida", "ok bye",
                    "bye bye", "dhanyavaad", "thank you bye",
                ],
                # Reduce latency: start generating reply as soon as the
                # caller pauses, rather than waiting for a definitive stop.
                "silenceTimeoutSeconds": 30,
                "maxDurationSeconds": 600,  # 10 minute hard cap
                "backgroundSound": "office",  # sounds natural, not studio
                "serverUrl": f"{self._config.webhook_base}/vapi",
                "serverUrlSecret": None,  # optional HMAC secret — add in production
            },
        }

    def _build_tools(self) -> list[dict[str, Any]]:
        """
        Define the functions the LLM can call during the conversation.

        Each tool has a name, description, and JSON Schema for parameters.
        The serverUrl on the assistant config tells Vapi where to POST
        function_call events.

        Returns:
            List of tool definition dicts.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": "send_whatsapp_hot_lead",
                    "description": (
                        "Send a WhatsApp message immediately when the lead shows clear buying intent. "
                        "Call this the moment you detect HOT signals — 'send me the details', "
                        "'when can you start', 'how much', 'let's do it'. "
                        "DO NOT wait for the call to end. "
                        "After calling this, tell the customer you have just sent them a WhatsApp."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "call_context": {
                                "type": "string",
                                "description": (
                                    "A human-sounding 3-4 sentence summary of what the customer "
                                    "wants: their business, what they sell, any budget or "
                                    "timeline mentioned, features they care about."
                                ),
                            },
                            "budget_mentioned": {
                                "type": "string",
                                "description": "The budget figure or range the customer mentioned, if any.",
                            },
                            "timeline": {
                                "type": "string",
                                "description": "When the customer wants the website live, if mentioned.",
                            },
                            "intent_signal": {
                                "type": "string",
                                "description": "The exact phrase that indicated high buying intent.",
                            },
                        },
                        "required": ["call_context", "intent_signal"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "book_callback",
                    "description": (
                        "Book a follow-up call when the customer asks to be called back at a specific time. "
                        "Parse natural language time references including vague ones like "
                        "'tomorrow morning' or 'after 5' or 'kal shaam' or 'Friday afternoon'. "
                        "After calling this, confirm the time out loud to the customer."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "raw_time_phrase": {
                                "type": "string",
                                "description": "Exactly what the customer said about timing.",
                            },
                            "resolved_datetime": {
                                "type": "string",
                                "description": (
                                    "Your best interpretation as an ISO datetime or readable string, "
                                    "e.g. '2026-08-23 10:00 IST' or 'Monday at 2 PM IST'."
                                ),
                            },
                            "customer_barrier": {
                                "type": "string",
                                "description": "The reason the customer is not proceeding now (budget, needs approval, timing).",
                            },
                        },
                        "required": ["raw_time_phrase", "resolved_datetime"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "end_call_summary",
                    "description": (
                        "Call this at the very end of every conversation, regardless of outcome. "
                        "Provide a final classification and full conversation summary. "
                        "This triggers the post-call WhatsApp and database logging."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "lead_status": {
                                "type": "string",
                                "enum": ["HOT", "WARM", "COLD"],
                                "description": "Final lead classification.",
                            },
                            "call_context": {
                                "type": "string",
                                "description": (
                                    "Full human-sounding summary of the conversation: "
                                    "business type, products, budget, timeline, features, concerns."
                                ),
                            },
                            "customer_name": {
                                "type": "string",
                                "description": "Customer's name if they mentioned it.",
                            },
                        },
                        "required": ["lead_status", "call_context"],
                    },
                },
            },
        ]
