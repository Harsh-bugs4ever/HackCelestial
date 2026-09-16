"""Business-day clock.

Operational questions ("today's occupancy", "tonight's roster") are asked in
the resort's local timezone, not the server's. The API used to call
``dt.date.today()``, which is the *process* timezone: a resort in Asia/Kolkata
running on a UTC host (Render, every major PaaS) saw the wrong business day
between 00:00 and 05:30 local every single night - exactly the night-audit
window where the numbers matter most.

Stored timestamps stay naive UTC (see ``models.utcnow``); only the *calendar
day* is localised. Keeping those two concerns apart means no stored data
changes when the resort timezone does.
"""
from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

log = logging.getLogger(__name__)
_FALLBACK = dt.timezone(dt.timedelta(hours=5, minutes=30), "IST")


def resort_tz() -> dt.tzinfo:
    """The resort's timezone, falling back to IST if the name is unusable.

    A bad TZ name must not take the API down - a wrong-but-running clock is
    recoverable, a boot loop on a config typo is not.
    """
    try:
        return ZoneInfo(settings.resort_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning(
            "unknown timezone %r; falling back to UTC+05:30", settings.resort_timezone
        )
        return _FALLBACK


def business_now() -> dt.datetime:
    """Current wall-clock time at the resort (timezone-aware)."""
    return dt.datetime.now(resort_tz())


def business_today() -> dt.date:
    """The operating day at the resort. Use instead of ``dt.date.today()``."""
    return business_now().date()


def to_business(moment: dt.datetime) -> dt.datetime:
    """Render a stored naive-UTC timestamp in resort local time."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return moment.astimezone(resort_tz())
