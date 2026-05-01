"""FRED — `https://api.stlouisfed.org/fred/releases/dates`.

Single source covering 95%+ of US high-impact events (CPI, NFP, PCE, GDP,
PPI, FOMC, retail sales, ISM). Free API key obtained via
https://fredaccount.stlouisfed.org/apikeys (~30s, no payment).

Spec : docs/vol_trading_pca/events_pipeline_spec.md §4.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from api.orchestration.events.sources.base import EventSource, Impact, RawEvent

# Whitelist of FRED release_id → (event_type, impact). Verified via
# GET /fred/releases?api_key=KEY&order_by=popularity. Update if FRED
# adds/removes a release we care about.
FRED_HIGH_IMPACT_RELEASES: dict[int, tuple[str, Impact]] = {
    10:  ("CPI", "high"),           # Consumer Price Index (BLS)
    50:  ("NFP", "high"),           # Employment Situation (BLS)
    21:  ("M2", "medium"),          # H.6 Money Stock (Fed)
    53:  ("GDP", "high"),           # GDP (BEA)
    151: ("PCE", "high"),           # Personal Income & Outlays (BEA)
    46:  ("PPI", "medium"),         # Producer Price Index (BLS)
    101: ("FOMC", "high"),          # H.15 Selected Interest Rates (Fed)
    175: ("RetailSales", "medium"), # Advance Monthly Retail Sales (Census)
    82:  ("ISM_Mfg", "medium"),     # ISM Manufacturing
}

# Approximate release time per event_type, in US/Eastern (DST aware via zoneinfo).
# All BLS/BEA stats land at 8:30 ET ; FOMC statement at 14:00 ET.
RELEASE_TIME_LOCAL: dict[str, tuple[int, int]] = {
    "CPI":         (8, 30),
    "NFP":         (8, 30),
    "PCE":         (8, 30),
    "GDP":         (8, 30),
    "PPI":         (8, 30),
    "RetailSales": (8, 30),
    "ISM_Mfg":     (10, 0),
    "M2":          (16, 30),
    "FOMC":        (14, 0),
}


class FREDSource(EventSource):
    name = "FRED"
    timeout_seconds = 15.0
    expected_min_events = 5  # ~5+ high-impact releases sur 6 mois minimum

    BASE_URL = "https://api.stlouisfed.org/fred"

    def __init__(self, api_key: str, horizon_days: int = 180):
        self.api_key = api_key
        self.horizon_days = horizon_days

    async def fetch(self) -> list[RawEvent]:
        end = (date.today() + timedelta(days=self.horizon_days)).isoformat()
        start = date.today().isoformat()
        params = {
            "api_key": self.api_key,
            "file_type": "json",
            "realtime_start": start,
            "realtime_end": end,
            "include_release_dates_with_no_data": "true",
            "limit": 10000,
            "sort_order": "asc",
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(f"{self.BASE_URL}/releases/dates", params=params)
            resp.raise_for_status()
        return self._parse(resp.json())

    def _parse(self, payload: dict) -> list[RawEvent]:
        out: list[RawEvent] = []
        for r in payload.get("release_dates", []):
            release_id = r.get("release_id")
            if release_id not in FRED_HIGH_IMPACT_RELEASES:
                continue
            event_type, impact = FRED_HIGH_IMPACT_RELEASES[release_id]
            date_str = r.get("date")
            if not isinstance(date_str, str):
                continue
            scheduled_at = self._localize_us_release(date_str, event_type)
            if scheduled_at is None:
                continue
            out.append(RawEvent(
                event_type=event_type, region="US", impact=impact,
                scheduled_at=scheduled_at,
                description=r.get("release_name", event_type),
                source_name=self.name,
            ))
        return out

    @staticmethod
    def _localize_us_release(date_str: str, event_type: str) -> datetime | None:
        """``date_str`` = ``YYYY-MM-DD``. Returns tz-aware UTC datetime."""
        try:
            y, m, d = date_str.split("-")
            local_d = date(int(y), int(m), int(d))
        except (ValueError, AttributeError):
            return None
        h, mi = RELEASE_TIME_LOCAL.get(event_type, (8, 30))
        local_dt = datetime(
            local_d.year, local_d.month, local_d.day, h, mi,
            tzinfo=ZoneInfo("America/New_York"),
        )
        return local_dt.astimezone(UTC)
