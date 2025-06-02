"""
Configuration management for ElevateBox Voice Agent.

All configurable values come from environment variables — no hardcoded secrets.
Fail fast at startup if required vars are missing.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Config:
    """
    Central configuration object. Built once at startup from environment.
    Raises ValueError immediately if any required key is missing.
    """

    # --- Vapi (voice AI platform) ---
    vapi_api_key: str
    vapi_phone_number_id: str          # Twilio number imported into Vapi

    # --- OpenAI (LLM brain) ---
    openai_api_key: str
    openai_model: str = "gpt-4o"       # change to gpt-4o-mini to cut cost

    # --- Twilio (WhatsApp) ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    # Sandbox number for testing, production number for live
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    # --- Target (the person to call) ---
    target_phone: str = "+918688664337"
    target_whatsapp: str = "whatsapp:+918688664337"

    # --- Candidate details sent in WhatsApp ---
    candidate_name: str = ""
    candidate_mobile: str = ""
    candidate_resume_url: str = ""      # publicly accessible URL to resume PDF
    architecture_image_url: str = ""    # publicly accessible URL to arch diagram

    # --- Hosting ---
    base_url: str = ""                  # e.g. https://your-app.onrender.com
    port: int = 8000

    # --- Runtime behaviour ---
    log_level: str = "INFO"
    environment: str = "production"     # "development" | "production"

    @classmethod
    def from_env(cls) -> "Config":
        """
        Build Config from environment variables.
        Raises ValueError with a clear message if required keys are absent.
        """
        required = {
            "VAPI_API_KEY": "Vapi API key",
            "VAPI_PHONE_NUMBER_ID": "Vapi phone number ID (your Twilio number in Vapi)",
            "OPENAI_API_KEY": "OpenAI API key",
        }
        missing = [f"{k} ({desc})" for k, desc in required.items() if not os.environ.get(k)]
        if missing:
            raise ValueError(
                "Missing required environment variables:\n" + "\n".join(f"  - {m}" for m in missing)
            )

        cfg = cls(
            vapi_api_key=os.environ["VAPI_API_KEY"],
            vapi_phone_number_id=os.environ["VAPI_PHONE_NUMBER_ID"],
            openai_api_key=os.environ["OPENAI_API_KEY"],
            openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID", ""),
            twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN", ""),
            twilio_whatsapp_from=os.environ.get(
                "TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"
            ),
            target_phone=os.environ.get("TARGET_PHONE", "+918688664337"),
            target_whatsapp=os.environ.get("TARGET_WHATSAPP", "whatsapp:+918688664337"),
            candidate_name=os.environ.get("CANDIDATE_NAME", ""),
            candidate_mobile=os.environ.get("CANDIDATE_MOBILE", ""),
            candidate_resume_url=os.environ.get("CANDIDATE_RESUME_URL", ""),
            architecture_image_url=os.environ.get("ARCHITECTURE_IMAGE_URL", ""),
            base_url=os.environ.get("BASE_URL", "").rstrip("/"),
            port=int(os.environ.get("PORT", "8000")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            environment=os.environ.get("ENVIRONMENT", "production"),
        )

        # Warn about optional but important vars, without logging their values
        optional_warnings = [
            ("TWILIO_ACCOUNT_SID", cfg.twilio_account_sid, "WhatsApp messages will not send"),
            ("CANDIDATE_MOBILE", cfg.candidate_mobile, "Your number won't appear in WhatsApp"),
            ("CANDIDATE_RESUME_URL", cfg.candidate_resume_url, "Resume won't attach to WhatsApp"),
            ("BASE_URL", cfg.base_url, "Vapi webhooks need a public URL to call back"),
        ]
        for key, val, consequence in optional_warnings:
            if not val:
                logger.warning("Optional env var %s not set — %s", key, consequence)

        return cfg

    @property
    def webhook_base(self) -> str:
        """Base URL Vapi uses to send webhook events to this server."""
        return f"{self.base_url}/webhook" if self.base_url else ""

    @property
    def whatsapp_configured(self) -> bool:
        """True when Twilio credentials are present and WhatsApp can send."""
        return bool(self.twilio_account_sid and self.twilio_auth_token)
