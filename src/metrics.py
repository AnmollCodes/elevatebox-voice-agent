"""
In-process call metrics and Twilio circuit breaker.

Why this module exists
----------------------
Three problems hit production voice agents quickly:

1. You don't know what's actually happening.
   Without metrics, you're flying blind. Is the HOT rate improving? Are most
   leads calling in Telugu? Are callbacks booking mostly for mornings? None of
   that is knowable from logs alone. This module exposes a /analytics endpoint
   with aggregated per-call data so you can answer those questions without
   shipping a whole data warehouse.

2. Twilio failures cascade silently.
   If Twilio is down or rate-limiting you, every call triggers a failed HTTP
   request, a caught exception, and a "False" return value. The caller hangs up
   having received nothing. Without a circuit breaker, every subsequent call
   wastes time waiting for the Twilio timeout. With one, after N failures the
   circuit opens and fast-fails immediately, allowing the system to recover
   gracefully and re-enabling when Twilio comes back.

3. Someone will hammer /call with a script.
   The admin key protects it from anonymous callers, but a stolen key means
   unlimited outbound calls at $0.32 each. A simple in-process rate limiter on
   /call caps the blast radius.

Engineering note on "in-process"
---------------------------------
All state is in memory. This is appropriate for a single-server POC. Production
would move metrics to PostgreSQL (time-series aggregation), the circuit breaker
state to Redis (shared across instances), and rate limiting to a Redis token
bucket. Those are 3 lines changed in each place — the interfaces here are
designed so the swap is trivial.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .classifier import LeadStatus


# ---------------------------------------------------------------------------
# Per-call record
# ---------------------------------------------------------------------------

@dataclass
class CallRecord:
    """Immutable snapshot of a single call's outcome."""

    call_id: str
    phone_masked: str              # first 6 digits + ****
    started_at: float              # Unix timestamp (time.time())
    ended_at: Optional[float] = None
    duration_seconds: Optional[float] = None
    lead_status: Optional[LeadStatus] = None
    persona: Optional[str] = None
    language: Optional[str] = None
    budget_detected: Optional[str] = None
    mid_call_whatsapp_sent: bool = False
    post_call_whatsapp_sent: bool = False
    callback_scheduled: bool = False
    classification_confidence: Optional[float] = None

    def finalise(
        self,
        lead_status: LeadStatus,
        persona: Optional[str] = None,
        language: Optional[str] = None,
        budget_detected: Optional[str] = None,
        mid_call_whatsapp_sent: bool = False,
        post_call_whatsapp_sent: bool = False,
        callback_scheduled: bool = False,
        classification_confidence: Optional[float] = None,
    ) -> None:
        """Mutate to record call outcome (called once per call)."""
        self.ended_at = time.time()
        self.duration_seconds = self.ended_at - self.started_at
        self.lead_status = lead_status
        self.persona = persona
        self.language = language
        self.budget_detected = budget_detected
        self.mid_call_whatsapp_sent = mid_call_whatsapp_sent
        self.post_call_whatsapp_sent = post_call_whatsapp_sent
        self.callback_scheduled = callback_scheduled
        self.classification_confidence = classification_confidence


# ---------------------------------------------------------------------------
# Metrics store
# ---------------------------------------------------------------------------

@dataclass
class MetricsStore:
    """
    Append-only log of call records + derived aggregates.

    Thread safety: not strictly thread-safe, but asyncio's single-threaded
    event loop means concurrent mutation of Python data structures doesn't race.
    """

    _records: list[CallRecord] = field(default_factory=list)

    def record_call_start(self, call_id: str, phone: str) -> CallRecord:
        """Create and register a new call record. Returns the mutable record."""
        masked = phone[:6] + "****" if len(phone) >= 6 else phone
        record = CallRecord(
            call_id=call_id,
            phone_masked=masked,
            started_at=time.time(),
        )
        self._records.append(record)
        return record

    def get(self, call_id: str) -> Optional[CallRecord]:
        """Look up a call record by call_id."""
        for r in self._records:
            if r.call_id == call_id:
                return r
        return None

    def summary(self) -> dict:
        """
        Aggregate metrics for /analytics endpoint.

        Returns a dict safe to JSON-serialise directly.
        """
        total = len(self._records)
        completed = [r for r in self._records if r.ended_at is not None]
        n = len(completed)

        # Status distribution
        status_counts: dict[str, int] = defaultdict(int)
        for r in completed:
            key = r.lead_status.value if r.lead_status else "unknown"
            status_counts[key] += 1

        # Persona distribution
        persona_counts: dict[str, int] = defaultdict(int)
        for r in completed:
            persona_counts[r.persona or "unknown"] += 1

        # Language distribution
        lang_counts: dict[str, int] = defaultdict(int)
        for r in completed:
            lang_counts[r.language or "unknown"] += 1

        # Averages
        durations = [r.duration_seconds for r in completed if r.duration_seconds]
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        confidences = [r.classification_confidence for r in completed if r.classification_confidence]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Action rates
        whatsapp_mid_call = sum(1 for r in completed if r.mid_call_whatsapp_sent)
        whatsapp_post_call = sum(1 for r in completed if r.post_call_whatsapp_sent)
        callbacks = sum(1 for r in completed if r.callback_scheduled)
        budgets_detected = sum(1 for r in completed if r.budget_detected)

        return {
            "total_calls": total,
            "completed_calls": n,
            "in_progress": total - n,
            "lead_status_distribution": dict(status_counts),
            "persona_distribution": dict(persona_counts),
            "language_distribution": dict(lang_counts),
            "avg_call_duration_seconds": round(avg_duration, 1),
            "avg_classification_confidence": round(avg_confidence, 3),
            "whatsapp_mid_call_fired": whatsapp_mid_call,
            "whatsapp_post_call_sent": whatsapp_post_call,
            "callbacks_scheduled": callbacks,
            "budget_detected_in": budgets_detected,
            "hot_rate": round(status_counts.get("hot", 0) / n, 3) if n else 0.0,
            "warm_rate": round(status_counts.get("warm", 0) / n, 3) if n else 0.0,
            "cold_rate": round(status_counts.get("cold", 0) / n, 3) if n else 0.0,
        }

    def recent(self, limit: int = 20) -> list[dict]:
        """Return the most recent `limit` call records as dicts."""
        slice_ = self._records[-limit:]
        return [
            {
                "call_id": r.call_id,
                "phone": r.phone_masked,
                "started_at": r.started_at,
                "duration_s": r.duration_seconds,
                "status": r.lead_status.value if r.lead_status else None,
                "persona": r.persona,
                "language": r.language,
                "budget": r.budget_detected,
                "confidence": r.classification_confidence,
                "whatsapp_mid_call": r.mid_call_whatsapp_sent,
                "whatsapp_post_call": r.post_call_whatsapp_sent,
                "callback": r.callback_scheduled,
            }
            for r in reversed(slice_)
        ]


# Module-level singleton — imported by main.py and call_handler.py
metrics = MetricsStore()


# ---------------------------------------------------------------------------
# Circuit breaker for Twilio
# ---------------------------------------------------------------------------

class CircuitState(str, Enum):
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing — fast-fail all requests
    HALF_OPEN = "half_open" # Testing recovery — one request let through


@dataclass
class CircuitBreaker:
    """
    Half-open circuit breaker for Twilio API calls.

    State machine:
      CLOSED → OPEN  when failure_threshold consecutive failures occur
      OPEN → HALF_OPEN  after recovery_timeout_seconds
      HALF_OPEN → CLOSED  on next success
      HALF_OPEN → OPEN  on next failure (back to full backoff)

    Usage:
        breaker = CircuitBreaker(name="twilio")

        async def send_whatsapp(...):
            if not breaker.allow_request():
                logger.warning("Circuit open — skipping Twilio call")
                return False
            try:
                result = await _twilio_send(...)
                breaker.record_success()
                return result
            except Exception as exc:
                breaker.record_failure()
                raise
    """

    name: str
    failure_threshold: int = 3            # consecutive failures to open
    recovery_timeout_seconds: float = 30.0 # time before trying half-open
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: Optional[float] = field(default=None, init=False)

    def allow_request(self) -> bool:
        """
        Returns True if the request should proceed.
        CLOSED → always True.
        OPEN → False unless recovery timeout has elapsed (then HALF_OPEN).
        HALF_OPEN → True for exactly one probe request.
        """
        if self._state == CircuitState.CLOSED:
            return True

        if self._state == CircuitState.OPEN:
            if self._opened_at and time.time() - self._opened_at >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                return True
            return False

        # HALF_OPEN — let one probe through
        return True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._state == CircuitState.HALF_OPEN or self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.time()

    @property
    def state(self) -> CircuitState:
        return self._state

    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN

    def status_dict(self) -> dict:
        return {
            "name": self.name,
            "state": self._state.value,
            "consecutive_failures": self._consecutive_failures,
            "opened_at": self._opened_at,
        }


# Module-level circuit breaker — shared by whatsapp.py
twilio_breaker = CircuitBreaker(name="twilio", failure_threshold=3, recovery_timeout_seconds=30.0)


# ---------------------------------------------------------------------------
# Rate limiter for /call endpoint
# ---------------------------------------------------------------------------

class RateLimiter:
    """
    Sliding-window rate limiter for the /call endpoint.

    Allows at most `max_calls` calls per `window_seconds`.
    Implementation: deque of timestamps, drop entries older than window.

    Not Redis-backed, so this protects a single instance only.
    For multi-instance deployments, replace with Redis ZRANGEBYSCORE.
    """

    def __init__(self, max_calls: int = 5, window_seconds: float = 60.0) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()

    def allow(self) -> bool:
        """Returns True if the request is within the rate limit."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Drop expired entries
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

        if len(self._timestamps) >= self.max_calls:
            return False

        self._timestamps.append(now)
        return True

    def remaining(self) -> int:
        """How many more calls are allowed in the current window."""
        now = time.time()
        cutoff = now - self.window_seconds
        active = sum(1 for t in self._timestamps if t >= cutoff)
        return max(0, self.max_calls - active)

    def reset_after_seconds(self) -> float:
        """Seconds until the oldest request in the window expires."""
        if not self._timestamps:
            return 0.0
        oldest = self._timestamps[0]
        return max(0.0, (oldest + self.window_seconds) - time.time())


# 5 outbound calls per 60 seconds — generous enough for testing, protective in prod
call_rate_limiter = RateLimiter(max_calls=5, window_seconds=60.0)
