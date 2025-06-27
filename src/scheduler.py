"""
Callback scheduling.

Converts natural language time phrases ("call me tomorrow morning",
"after 5 PM on Friday") into actual datetime objects and schedules
a follow-up call using APScheduler.

For a POC this uses an in-memory store — acceptable since we document
the limitation. Production would use APScheduler with a PostgreSQL job store
or a dedicated queue (Celery + Redis).
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore

logger = logging.getLogger(__name__)

# All times are treated as IST (Asia/Kolkata)
IST = ZoneInfo("Asia/Kolkata")

# Singleton scheduler — started once when the app boots
_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """
    Return the singleton APScheduler instance.
    Initialised lazily on first call.
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone=IST,
        )
    return _scheduler


def start_scheduler() -> None:
    """Start the background scheduler. Called once at app startup."""
    sched = get_scheduler()
    if not sched.running:
        sched.start()
        logger.info("APScheduler started")


def stop_scheduler() -> None:
    """Graceful shutdown. Called at app teardown."""
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)
        logger.info("APScheduler stopped")


# ---------------------------------------------------------------------------
# Natural language → datetime resolution
# ---------------------------------------------------------------------------

_TIME_WORDS = {
    "morning": 9,
    "afternoon": 14,
    "evening": 18,
    "night": 20,
    "noon": 12,
    "midday": 12,
    "subah": 9,      # Hindi
    "dopahar": 14,
    "shaam": 18,
    "raat": 20,
    "udayam": 9,     # Telugu
    "madhyannam": 12,
    "saayantram": 18,
}

_DAY_OFFSETS = {
    "today": 0,
    "tonight": 0,
    "tomorrow": 1,
    "kal": 1,        # Hindi / Telugu overlap
    "parso": 2,      # Hindi: day after tomorrow
    "monday": None,
    "tuesday": None,
    "wednesday": None,
    "thursday": None,
    "friday": None,
    "saturday": None,
    "sunday": None,
}

_WEEKDAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
]


def resolve_callback_time(phrase: str, reference_now: Optional[datetime] = None) -> Optional[datetime]:
    """
    Convert a natural language callback phrase into a timezone-aware datetime.

    Examples:
        "call me tomorrow morning"  → next day at 09:00 IST
        "after 5 PM"                → today at 17:00 IST (or tomorrow if past)
        "Friday afternoon"          → next Friday at 14:00 IST
        "kal subah"                 → tomorrow at 09:00 IST

    Args:
        phrase: Raw phrase from the customer, e.g. "call me back tomorrow evening".
        reference_now: Override the current time (used in tests). Defaults to now in IST.

    Returns:
        Timezone-aware datetime in IST, or None if parsing fails.
    """
    now = reference_now or datetime.now(IST)
    text = phrase.lower().strip()

    target_date: Optional[datetime] = None
    target_hour: Optional[int] = None
    target_minute: int = 0

    # --- Resolve the day ---
    for word, offset in _DAY_OFFSETS.items():
        if word in text:
            if offset is not None:
                target_date = now + timedelta(days=offset)
            else:
                # Named weekday
                target_weekday = _WEEKDAY_NAMES.index(word)
                days_ahead = (target_weekday - now.weekday()) % 7
                if days_ahead == 0:
                    days_ahead = 7   # always go forward
                target_date = now + timedelta(days=days_ahead)
            break

    if target_date is None:
        target_date = now   # default to today

    # --- Resolve the time of day ---
    # Named time words
    for word, hour in _TIME_WORDS.items():
        if word in text:
            target_hour = hour
            break

    # Explicit clock times: "3 PM", "15:30", "5:30 pm"
    if target_hour is None:
        clock_match = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
        if clock_match:
            h = int(clock_match.group(1))
            m = int(clock_match.group(2) or 0)
            meridiem = clock_match.group(3)
            if meridiem == "pm" and h < 12:
                h += 12
            elif meridiem == "am" and h == 12:
                h = 0
            target_hour = h
            target_minute = m

    if target_hour is None:
        target_hour = 10    # fallback: 10 AM

    resolved = target_date.replace(
        hour=target_hour,
        minute=target_minute,
        second=0,
        microsecond=0,
        tzinfo=IST,
    )

    # If the resolved time is already in the past (within the same day), push to next day
    if resolved <= now:
        resolved += timedelta(days=1)

    logger.info(
        "Resolved callback phrase %r → %s",
        phrase,
        resolved.isoformat(),
    )
    return resolved


def schedule_callback(
    phone_number: str,
    callback_time: datetime,
    call_context: str,
    callback_fn,
) -> str:
    """
    Schedule a follow-up call at the specified time.

    Args:
        phone_number: E.164 format phone number to call back.
        callback_time: Timezone-aware datetime for the call.
        call_context: Summary of what was discussed, passed to the callback.
        callback_fn: Async function to invoke when the job fires.
                     Signature: async (phone_number: str, context: str) -> None

    Returns:
        APScheduler job ID string.
    """
    sched = get_scheduler()
    job = sched.add_job(
        callback_fn,
        trigger="date",
        run_date=callback_time,
        kwargs={"phone_number": phone_number, "context": call_context},
        id=f"callback_{phone_number}_{int(callback_time.timestamp())}",
        replace_existing=True,
        misfire_grace_time=300,   # fire up to 5 min late if server was briefly down
    )
    logger.info(
        "Scheduled callback for %s at %s (job_id=%s)",
        phone_number,
        callback_time.isoformat(),
        job.id,
    )
    return job.id


def list_scheduled_callbacks() -> list[dict]:
    """
    Return all pending callback jobs as dicts for health/debug endpoints.
    Never includes PII beyond phone number and scheduled time.
    """
    sched = get_scheduler()
    jobs = []
    for job in sched.get_jobs():
        next_run = job.next_run_time
        jobs.append({
            "job_id": job.id,
            "next_run_utc": next_run.isoformat() if next_run else None,
        })
    return jobs
