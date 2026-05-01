"""Eurostat — euro area monthly stats : HICP flash, HICP final, GDP flash.

Strategy : computed schedule, no scraping. Eurostat releases follow stable
calendar conventions :
  - HICP flash       : last business day of M+0 at 11:00 CET (release for month M)
  - HICP final       : ~17th of M+1 at 11:00 CET
  - GDP flash (Q)    : ~30 days after end of quarter at 11:00 CET
  - Unemployment     : ~3rd day of M+2 at 11:00 CET

We compute the next 6 months of releases via these rules. Drift vs official
calendar is typically ±1-2 days — acceptable for the J-5 dampener.

If precision matters more later, replace by a parser of
``https://ec.europa.eu/eurostat/news/release-calendar``.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from api.orchestration.events.sources.base import EventSource, RawEvent

TZ_BRUSSELS = ZoneInfo("Europe/Brussels")
RELEASE_HOUR_LOCAL = 11
HORIZON_MONTHS = 6


def _last_business_day(year: int, month: int) -> int:
    """Last weekday (Mon-Fri) of (year, month)."""
    last = calendar.monthrange(year, month)[1]
    while datetime(year, month, last).weekday() >= 5:
        last -= 1
    return last


def _at_release_time(d: datetime) -> datetime:
    """Localize a date at the canonical release time, return UTC tz-aware."""
    local = d.replace(hour=RELEASE_HOUR_LOCAL, minute=0, second=0, microsecond=0, tzinfo=TZ_BRUSSELS)
    return local.astimezone(ZoneInfo("UTC"))


class EurostatSource(EventSource):
    name = "Eurostat"
    timeout_seconds = 1.0
    expected_min_events = 6  # at minimum 1 HICP flash + 1 final + assorted on horizon

    async def fetch(self) -> list[RawEvent]:
        now = datetime.now(tz=ZoneInfo("UTC"))
        today = now.astimezone(TZ_BRUSSELS).date()
        out: list[RawEvent] = []
        for k in range(HORIZON_MONTHS + 1):
            # Walk months relative to current.
            year = today.year + (today.month + k - 1) // 12
            month = (today.month + k - 1) % 12 + 1

            # 1. HICP flash : last business day of the month, at 11:00 CET.
            day = _last_business_day(year, month)
            ts = _at_release_time(datetime(year, month, day))
            if ts > now:
                out.append(RawEvent(
                    event_type="CPI_FLASH_EU", region="EU", impact="high",
                    scheduled_at=ts,
                    description="Eurozone HICP flash estimate",
                    source_name=self.name,
                ))

            # 2. HICP final : ~17th of the month, at 11:00 CET.
            try:
                ts = _at_release_time(datetime(year, month, 17))
                if ts > now:
                    out.append(RawEvent(
                        event_type="CPI_EU", region="EU", impact="high",
                        scheduled_at=ts,
                        description="Eurozone HICP final",
                        source_name=self.name,
                    ))
            except ValueError:
                pass

            # 3. GDP flash : ~30 days after quarter end (Jan, Apr, Jul, Oct).
            if month in (1, 4, 7, 10):
                # First Tuesday after the 28th approx (close enough at ±2d).
                try:
                    ts = _at_release_time(datetime(year, month, 30))
                    if ts > now:
                        out.append(RawEvent(
                            event_type="GDP_EU", region="EU", impact="high",
                            scheduled_at=ts,
                            description="Eurozone GDP flash estimate",
                            source_name=self.name,
                        ))
                except ValueError:
                    pass

            # 4. Unemployment : ~3rd of M+2, low impact for vol context — skip.
            _ = timedelta()  # placeholder
        return out
