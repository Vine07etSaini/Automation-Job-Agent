"""Parse portal 'posted' strings ("3 days ago", "Just now", "30+ days ago") into IST datetimes."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from dateutil import parser as dateparser

from job_copilot.config import IST

_UNITS = {
    "minute": timedelta(minutes=1),
    "min": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "hr": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),
}


def parse_posted(text: str | None, now: datetime | None = None) -> datetime | None:
    """Convert a relative or absolute posting date into an aware IST datetime. None if unknown."""
    if not text:
        return None
    now = now or datetime.now(IST)
    t = text.lower().strip()
    t = re.sub(r"^(posted|active|reposted)\s*(on|:)?\s*", "", t).strip()

    if t in {"just now", "today", "few hours ago", "a few hours ago", "just posted", "now"}:
        return now
    if t == "yesterday":
        return now - timedelta(days=1)

    m = re.match(r"(\d+|an?|one)\s*(\+)?\s*(minute|min|hour|hr|day|week|month)s?\s*(\+)?\s*ago", t)
    if m:
        qty = 1 if m.group(1) in {"a", "an", "one"} else int(m.group(1))
        plus = bool(m.group(2) or m.group(4))
        # "30+ days ago" means *at least* 31 days old
        return now - _UNITS[m.group(3)] * (qty + (1 if plus else 0))

    try:
        dt = dateparser.parse(text, fuzzy=True, dayfirst=True, default=now.replace(tzinfo=None))
    except (ValueError, OverflowError):
        return None
    dt = dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
    # A date without a year that lands in the future belongs to last year
    if dt > now + timedelta(days=1):
        dt = dt.replace(year=dt.year - 1)
    return dt


def is_fresh(posted_at: datetime | None, max_age_days: int, now: datetime | None = None) -> bool | None:
    """True/False if the age is known, None if the date is unknown."""
    if posted_at is None:
        return None
    now = now or datetime.now(IST)
    return now - posted_at <= timedelta(days=max_age_days)
