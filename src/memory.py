"""
Lead memory — persistent context across multiple calls to the same phone number.

Without memory, every call starts from zero. Priya can't say "Last time you mentioned
budget was the concern — has that changed?" because she doesn't know there was a
last time. This module fixes that.

Architecture: in-memory dict as the default store (zero dependencies, works everywhere).
The same interface is exposed for a Redis-backed store — swap the backend by setting
REDIS_URL in .env. The in-memory store survives restarts only through the process
lifetime, which is fine for demos and single-server deployments. For production
multi-instance deployments, set REDIS_URL and the Redis backend kicks in automatically.

What gets stored per lead:
  - call history (up to 20 calls, FIFO)
  - last status (HOT/WARM/COLD)
  - primary objection from most recent call
  - persona from most recent call
  - number of calls made
  - last call timestamp

What Priya gets back:
  - context_snippet  — one sentence about the last call, ready to inject into the prompt
  - is_returning     — bool, True if we've spoken before
  - previous_status  — the classification from last time
  - call_count       — total calls made to this number
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# CallRecord — one entry in the history
# ---------------------------------------------------------------------------

@dataclass
class CallRecord:
    """A snapshot of one completed call, stored in lead history."""
    call_id: str
    timestamp: float          # unix epoch
    status: str               # "HOT" | "WARM" | "COLD"
    confidence: float
    primary_objection: str    # "price" | "timing" | "trust" | "competition" | "none"
    persona: str              # "Executive" | "Explorer" | "Budget-Constrained" | "Time-Pressured"
    arc: str                  # e.g. "cold→warm→hot"
    momentum: float
    quality_score: int
    transcript_snippet: str   # first 200 chars of transcript, for quick review

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# LeadMemory entry — everything we know about one phone number
# ---------------------------------------------------------------------------

@dataclass
class LeadProfile:
    """Complete memory for a single lead (phone number)."""
    phone: str
    call_count: int = 0
    last_status: str = "UNKNOWN"
    last_objection: str = "none"
    last_persona: str = "unknown"
    last_call_ts: float = 0.0
    history: list[CallRecord] = field(default_factory=list)

    # ---- derived helpers ------------------------------------------------

    @property
    def is_returning(self) -> bool:
        return self.call_count > 0

    @property
    def days_since_last_call(self) -> Optional[float]:
        if self.last_call_ts == 0:
            return None
        return (time.time() - self.last_call_ts) / 86400

    def context_snippet(self) -> str:
        """
        One sentence injected into Priya's system prompt at the start of a call.
        Tells Priya what happened last time so she can personalise the opening.
        """
        if not self.is_returning:
            return ""

        days = self.days_since_last_call
        if days is None:
            when = "previously"
        elif days < 1:
            when = "earlier today"
        elif days < 2:
            when = "yesterday"
        elif days < 7:
            when = f"{int(days)} days ago"
        elif days < 30:
            when = f"{int(days / 7)} week{'s' if days >= 14 else ''} ago"
        else:
            when = f"{int(days / 30)} month{'s' if days >= 60 else ''} ago"

        status_map = {
            "HOT":  "was very interested",
            "WARM": "showed some interest",
            "COLD": "wasn't interested at the time",
        }
        status_phrase = status_map.get(self.last_status, "spoke with us")

        objection_map = {
            "price":       "their main concern was the price",
            "timing":      "timing wasn't right for them",
            "trust":       "they wanted to see more examples",
            "competition": "they were considering another option",
            "none":        "",
        }
        obj_phrase = objection_map.get(self.last_objection, "")

        parts = [f"This person called {when} and {status_phrase}."]
        if obj_phrase:
            parts.append(f"Note: {obj_phrase}.")
        if self.call_count > 1:
            parts.append(f"This is their call #{self.call_count + 1}.")

        return " ".join(parts)

    def priya_opener(self) -> str:
        """
        A personalised opening line Priya can use for returning leads.
        Returns empty string for first-time leads.
        """
        if not self.is_returning:
            return ""

        if self.last_status == "HOT":
            return (
                "Arre, aap phir se call kiye! Bahut accha laga. "
                "Last time aap bahut interested the — shall we pick up from where we left off?"
            )
        if self.last_objection == "price":
            return (
                "Hi! Last time aapne budget ke baare mein baat ki thi. "
                "Has that changed? We now have a ₹15k starter that might work for you."
            )
        if self.last_objection == "timing":
            return (
                "Hi! You'd mentioned you were busy last time. "
                "Hope this is a better moment? I'll keep it quick."
            )
        if self.last_status == "COLD":
            return (
                "Hi! We spoke before and I know you weren't interested then. "
                "I just wanted to share one quick thing that might change that — give me 60 seconds?"
            )
        return (
            "Hi! Good to connect again. "
            "Last time we chatted about your online store — shall we continue?"
        )

    def as_dict(self) -> dict:
        return {
            "phone": self.phone,
            "call_count": self.call_count,
            "is_returning": self.is_returning,
            "last_status": self.last_status,
            "last_objection": self.last_objection,
            "last_persona": self.last_persona,
            "last_call_ts": self.last_call_ts,
            "days_since_last_call": self.days_since_last_call,
            "context_snippet": self.context_snippet(),
            "priya_opener": self.priya_opener(),
            "history": [r.as_dict() for r in self.history[-5:]],  # last 5 calls
        }


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------

class _InMemoryStore:
    """Thread-safe (enough for single-process FastAPI) in-memory lead store."""

    def __init__(self) -> None:
        self._data: dict[str, LeadProfile] = {}

    def get(self, phone: str) -> LeadProfile:
        return self._data.get(phone, LeadProfile(phone=phone))

    def save(self, profile: LeadProfile) -> None:
        self._data[profile.phone] = profile

    def all_profiles(self) -> list[LeadProfile]:
        return list(self._data.values())

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# Redis backend (optional — activated when REDIS_URL is set)
# ---------------------------------------------------------------------------

class _RedisStore:
    """Redis-backed lead store for multi-instance production deployments."""

    _PREFIX = "eb:lead:"
    _TTL    = 60 * 60 * 24 * 180  # 6 months

    def __init__(self, redis_url: str) -> None:
        try:
            import redis  # type: ignore
            self._r = redis.from_url(redis_url, decode_responses=True)
            self._r.ping()  # fail fast if Redis is unreachable
        except Exception as exc:
            raise RuntimeError(f"Redis connection failed: {exc}") from exc

    def _key(self, phone: str) -> str:
        return self._PREFIX + phone.replace("+", "").replace(" ", "")

    def get(self, phone: str) -> LeadProfile:
        raw = self._r.get(self._key(phone))
        if not raw:
            return LeadProfile(phone=phone)
        data = json.loads(raw)
        profile = LeadProfile(
            phone=data["phone"],
            call_count=data.get("call_count", 0),
            last_status=data.get("last_status", "UNKNOWN"),
            last_objection=data.get("last_objection", "none"),
            last_persona=data.get("last_persona", "unknown"),
            last_call_ts=data.get("last_call_ts", 0.0),
        )
        profile.history = [
            CallRecord(**r) for r in data.get("history", [])
        ]
        return profile

    def save(self, profile: LeadProfile) -> None:
        data = {
            "phone": profile.phone,
            "call_count": profile.call_count,
            "last_status": profile.last_status,
            "last_objection": profile.last_objection,
            "last_persona": profile.last_persona,
            "last_call_ts": profile.last_call_ts,
            "history": [r.as_dict() for r in profile.history],
        }
        self._r.setex(self._key(profile.phone), self._TTL, json.dumps(data))

    def all_profiles(self) -> list[LeadProfile]:
        keys = self._r.keys(self._PREFIX + "*")
        profiles = []
        for k in keys:
            raw = self._r.get(k)
            if raw:
                data = json.loads(raw)
                profiles.append(self.get(data.get("phone", "")))
        return profiles

    def clear(self) -> None:
        keys = self._r.keys(self._PREFIX + "*")
        if keys:
            self._r.delete(*keys)

    def __len__(self) -> int:
        return len(self._r.keys(self._PREFIX + "*"))


# ---------------------------------------------------------------------------
# Public LeadMemory façade
# ---------------------------------------------------------------------------

class LeadMemory:
    """
    Public interface for storing and retrieving lead history across calls.

    Usage
    -----
    memory = LeadMemory()            # auto-selects backend from REDIS_URL env var

    # Retrieve context before a call
    profile = memory.get("+919876543210")
    if profile.is_returning:
        inject_into_prompt(profile.context_snippet())

    # Record the outcome after a call
    memory.record(
        phone="+919876543210",
        call_id="call-abc-123",
        status="HOT",
        confidence=0.94,
        primary_objection="price",
        persona="Budget-Constrained",
        arc="cold→warm→hot",
        momentum=0.6,
        quality_score=78,
        transcript_snippet="Agent: Hi, online store...",
    )
    """

    MAX_HISTORY = 20  # calls stored per lead

    def __init__(self, redis_url: Optional[str] = None) -> None:
        url = redis_url or os.getenv("REDIS_URL")
        if url:
            try:
                self._store: _InMemoryStore | _RedisStore = _RedisStore(url)
            except RuntimeError:
                # Redis failed — fall back to in-memory silently
                self._store = _InMemoryStore()
        else:
            self._store = _InMemoryStore()

    # ---- read --------------------------------------------------------

    def get(self, phone: str) -> LeadProfile:
        """Retrieve the full profile for a phone number (returns empty profile if unknown)."""
        return self._store.get(phone)

    # ---- write -------------------------------------------------------

    def record(
        self,
        phone: str,
        call_id: str,
        status: str,
        confidence: float,
        primary_objection: str = "none",
        persona: str = "unknown",
        arc: str = "",
        momentum: float = 0.0,
        quality_score: int = 0,
        transcript_snippet: str = "",
    ) -> LeadProfile:
        """
        Record the outcome of a completed call.

        Returns the updated LeadProfile.
        """
        profile = self._store.get(phone)
        profile.call_count += 1
        profile.last_status = status
        profile.last_objection = primary_objection
        profile.last_persona = persona
        profile.last_call_ts = time.time()

        record = CallRecord(
            call_id=call_id,
            timestamp=time.time(),
            status=status,
            confidence=confidence,
            primary_objection=primary_objection,
            persona=persona,
            arc=arc,
            momentum=momentum,
            quality_score=quality_score,
            transcript_snippet=transcript_snippet[:200],
        )
        profile.history.append(record)

        # Keep only the most recent MAX_HISTORY entries
        if len(profile.history) > self.MAX_HISTORY:
            profile.history = profile.history[-self.MAX_HISTORY:]

        self._store.save(profile)
        return profile

    # ---- introspection -----------------------------------------------

    def all_profiles(self) -> list[LeadProfile]:
        """Return all known lead profiles (useful for analytics)."""
        return self._store.all_profiles()

    def clear(self) -> None:
        """Wipe all memory (tests and demos only)."""
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


# ---------------------------------------------------------------------------
# Module-level singleton (imported by main.py and call_handler.py)
# ---------------------------------------------------------------------------

lead_memory = LeadMemory()
