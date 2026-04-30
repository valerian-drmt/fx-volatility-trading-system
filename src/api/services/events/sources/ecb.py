"""ECB Governing Council monetary policy meetings (rate decisions).

Strategy : hardcoded calendar instead of HTML scraping. ECB publishes the full
year of MPC dates 12+ months in advance, schedule rarely changes. Maintaining
a list here is more reliable than parsing
``https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html`` which
breaks every time the page is restyled.

Update : extend MPC_DATES once a year when ECB publishes the next year (~5 min).
Source of truth : https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from api.services.events.sources.base import EventSource, RawEvent

# Confirmed monetary policy meeting dates. Decision released 14:15 CET / 14:15 CEST,
# press conference at 14:45. Cf. ECB calendar (consulted 2026-04).
MPC_DATES: list[str] = [
    # 2026
    "2026-01-22", "2026-03-12", "2026-04-30",
    "2026-06-04", "2026-07-23", "2026-09-10",
    "2026-10-29", "2026-12-17",
    # 2027
    "2027-02-04", "2027-03-18", "2027-04-22",
    "2027-06-10", "2027-07-22", "2027-09-09",
    "2027-10-28", "2027-12-16",
]


class ECBSource(EventSource):
    name = "ECB"
    timeout_seconds = 1.0  # in-memory only
    expected_min_events = 4  # at least 4 meetings ahead at any point

    async def fetch(self) -> list[RawEvent]:
        out: list[RawEvent] = []
        tz = ZoneInfo("Europe/Berlin")
        now = datetime.now(tz=ZoneInfo("UTC"))
        for date_str in MPC_DATES:
            y, m, d = (int(x) for x in date_str.split("-"))
            local = datetime(y, m, d, 14, 15, tzinfo=tz)
            scheduled_at = local.astimezone(ZoneInfo("UTC"))
            if scheduled_at <= now:
                continue
            out.append(RawEvent(
                event_type="ECB",
                region="EU",
                impact="high",
                scheduled_at=scheduled_at,
                description="ECB Main Refinancing Rate decision + press conference",
                source_name=self.name,
            ))
        return out
