"""ONS — UK monthly stats : CPI, GDP, employment.

Strategy : computed schedule (cf. eurostat.py rationale). ONS conventions :
  - CPI : Wednesday of week containing 17th of M+1 at 07:00 UK time
  - GDP monthly : ~12th of M+2 at 07:00 UK time
  - Employment : ~12th of M+1 at 07:00 UK time

For an event-dampener at J-5 horizon, the ±2 days drift is acceptable.

Source : https://www.ons.gov.uk/releasecalendar
"""
from __future__ import annotations

import calendar
from datetime import datetime
from zoneinfo import ZoneInfo

from api.services.events.sources.base import EventSource, RawEvent

TZ_LONDON = ZoneInfo("Europe/London")
RELEASE_HOUR_LOCAL = 7
HORIZON_MONTHS = 6


def _wednesday_in_week_of(year: int, month: int, day: int) -> datetime:
    """Return Wednesday of the week containing (year, month, day)."""
    target = datetime(year, month, day)
    delta = (target.weekday() - 2) % 7  # Wed = 2
    if delta == 0:
        return target
    return target.replace(day=max(1, min(day - delta, calendar.monthrange(year, month)[1])))


def _at_release_time(d: datetime) -> datetime:
    local = d.replace(hour=RELEASE_HOUR_LOCAL, minute=0, second=0, microsecond=0, tzinfo=TZ_LONDON)
    return local.astimezone(ZoneInfo("UTC"))


class ONSSource(EventSource):
    name = "ONS"
    timeout_seconds = 1.0
    expected_min_events = 4

    async def fetch(self) -> list[RawEvent]:
        now = datetime.now(tz=ZoneInfo("UTC"))
        today = now.astimezone(TZ_LONDON).date()
        out: list[RawEvent] = []
        for k in range(HORIZON_MONTHS + 1):
            year = today.year + (today.month + k - 1) // 12
            month = (today.month + k - 1) % 12 + 1

            # CPI : Wednesday of week containing the 17th, 07:00 UK.
            try:
                wed = _wednesday_in_week_of(year, month, 17)
                ts = _at_release_time(wed)
                if ts > now:
                    out.append(RawEvent(
                        event_type="CPI_GB", region="GB", impact="high",
                        scheduled_at=ts,
                        description="UK CPI (ONS)",
                        source_name=self.name,
                    ))
            except ValueError:
                pass

            # GDP : ~12th of M+2 at 07:00 UK time. Take 12th of current month
            # (rough, but cheaper than tracking M+2 alignment).
            try:
                ts = _at_release_time(datetime(year, month, 12))
                if ts > now:
                    out.append(RawEvent(
                        event_type="GDP_GB", region="GB", impact="medium",
                        scheduled_at=ts,
                        description="UK GDP estimate (ONS)",
                        source_name=self.name,
                    ))
            except ValueError:
                pass
        return out
