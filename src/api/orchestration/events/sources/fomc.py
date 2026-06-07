"""FOMC meetings (complement to FRED H.15 release).

Why both : FRED gives the H.15 Selected Interest Rates release (which lands
the day of the FOMC) but does not name the FOMC meeting itself as a separate
event. The minutes (released ~3 weeks after the meeting) are also missing
from FRED. This source fills both gaps.

Strategy : hardcoded (cf. ecb.py). Fed publishes the full year's calendar
in advance.

Source : https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from api.orchestration.events.sources.base import EventSource, RawEvent

# 2-day FOMC meetings; the rate decision lands at 14:00 ET on day 2.
# Format : (meeting_day_2_iso). Minutes = day_2 + 21 days at 14:00 ET.
FOMC_MEETINGS: list[str] = [
    # 2026 (8 meetings, Day 2)
    "2026-01-28", "2026-03-18", "2026-04-29",
    "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-11-04", "2026-12-16",
    # 2027 — published late 2026
    "2027-01-27", "2027-03-17", "2027-04-28",
    "2027-06-16", "2027-07-28", "2027-09-22",
    "2027-11-03", "2027-12-15",
]


class FOMCSource(EventSource):
    name = "FOMC"
    timeout_seconds = 1.0
    expected_min_events = 4

    async def fetch(self) -> list[RawEvent]:
        tz_et = ZoneInfo("America/New_York")
        now = datetime.now(tz=ZoneInfo("UTC"))
        out: list[RawEvent] = []
        for date_str in FOMC_MEETINGS:
            y, m, d = (int(x) for x in date_str.split("-"))
            decision_local = datetime(y, m, d, 14, 0, tzinfo=tz_et)
            decision_utc = decision_local.astimezone(ZoneInfo("UTC"))
            if decision_utc > now:
                out.append(RawEvent(
                    event_type="FOMC", region="US", impact="high",
                    scheduled_at=decision_utc,
                    description="FOMC rate decision + statement",
                    source_name=self.name,
                ))
            # Minutes : ~3 weeks (21d) after, 14:00 ET.
            minutes_local = decision_local + timedelta(days=21)
            minutes_utc = minutes_local.astimezone(ZoneInfo("UTC"))
            if minutes_utc > now:
                out.append(RawEvent(
                    event_type="FOMC_minutes", region="US", impact="medium",
                    scheduled_at=minutes_utc,
                    description="FOMC meeting minutes release",
                    source_name=self.name,
                ))
        return out
