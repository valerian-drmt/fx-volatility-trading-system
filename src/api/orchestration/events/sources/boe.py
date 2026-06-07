"""Bank of England MPC rate decisions.

Strategy : hardcoded calendar (cf. ecb.py rationale). BoE publishes MPC dates
6+ months in advance.

Update : extend MPC_DATES once a year (~5 min).
Source of truth : https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from api.orchestration.events.sources.base import EventSource, RawEvent

# Decision announcement at 12:00 UK time. Cf. BoE upcoming MPC page (2026-04 snap).
MPC_DATES: list[str] = [
    # 2026 (8 meetings)
    "2026-02-05", "2026-03-19", "2026-04-30",
    "2026-06-18", "2026-07-30", "2026-09-17",
    "2026-11-05", "2026-12-17",
    # 2027 (TBC, BoE typically publishes Q4 of preceding year)
    "2027-02-04", "2027-03-18", "2027-05-06",
    "2027-06-17", "2027-08-05", "2027-09-16",
    "2027-11-04", "2027-12-16",
]


class BoESource(EventSource):
    name = "BoE"
    timeout_seconds = 1.0
    expected_min_events = 4

    async def fetch(self) -> list[RawEvent]:
        out: list[RawEvent] = []
        tz = ZoneInfo("Europe/London")
        now = datetime.now(tz=ZoneInfo("UTC"))
        for date_str in MPC_DATES:
            y, m, d = (int(x) for x in date_str.split("-"))
            local = datetime(y, m, d, 12, 0, tzinfo=tz)
            scheduled_at = local.astimezone(ZoneInfo("UTC"))
            if scheduled_at <= now:
                continue
            out.append(RawEvent(
                event_type="BOE",
                region="GB",
                impact="high",
                scheduled_at=scheduled_at,
                description="Bank of England Bank Rate decision",
                source_name=self.name,
            ))
        return out
