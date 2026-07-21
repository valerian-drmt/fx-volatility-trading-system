# Events pipeline — refactor spec

Goal: replace the current cascade (`TradingEconomics → ForexFactory`) with a multi-source aggregator of orthogonal sources — testable, isolated per source, idempotent.

At the end of this implementation: **a production-quality economic events retrieval service**, with no dependency on a commercial third-party aggregator, no manual intervention, and automatic daily refresh.

Three fundamental building blocks to deliver, in order, then 7 concrete sources plugged into them.

---

## 0. System

```
┌──────────────────────────────────────────────────────────────────┐
│                        EventsScheduler                           │
│  • async loop (24h interval, jitter ±10min)                      │
│  • runs every Source in parallel (asyncio.gather)                │
│  • isolates failures: 1 source down ≠ pipeline down              │
│  • collect → dedupe by hash → idempotent upsert                  │
└──────────────────────────────────────────────────────────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  Source A    │    │  Source B    │    │  Source N    │
│  (FRED)      │    │  (ECB)       │    │  (...)       │
│              │    │              │    │              │
│ fetch() →    │    │ fetch() →    │    │ fetch() →    │
│  [Event]     │    │  [Event]     │    │  [Event]     │
└──────────────┘    └──────────────┘    └──────────────┘
        │                    │                    │
        └────────────────────┴────────────────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  EventDeduplicator   │
                  │  hash(type, region,  │
                  │       scheduled_at)  │
                  └──────────────────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │  postgres.events     │
                  │  UNIQUE(event_hash)  │
                  │  ON CONFLICT SKIP    │
                  └──────────────────────┘
```

Constraints:

- a source that times out or crashes does not block the others
- a daily re-run must be a no-op if nothing has changed
- adding a new source = create 1 class, 0 changes to the scheduler
- every source is testable in isolation (mock HTTP)

---

## 1. `Source` pattern (deliverable 1)

### Interface

File: `src/api/orchestration/events/sources/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Region = Literal["US", "EU", "GB"]
Impact = Literal["high", "medium", "low"]

@dataclass(frozen=True)
class RawEvent:
    """Event as returned by a source, before dedupe/persist."""
    event_type: str          # "FOMC", "NFP", "CPI", ...
    region: Region
    impact: Impact
    scheduled_at: datetime   # UTC, tz-aware
    description: str
    source_name: str         # "FRED", "ECB", ... for debugging

class EventSource(ABC):
    """Contract for any economic events source."""

    name: str                       # unique identifier, used in logs
    timeout_seconds: float = 10.0   # hard cap, no source is allowed to drag
    expected_min_events: int = 1    # warn if fetch returns fewer than this

    @abstractmethod
    async def fetch(self) -> list[RawEvent]:
        """
        Fetches and parses the events.
        Must raise an exception on failure — the scheduler catches it.
        Do not swallow errors here.
        """
        ...
```

### I/O / parsing separation rule

Every source MUST separate:
- `fetch()`: network I/O only, returns the raw payload (HTML/JSON/XML)
- `_parse(payload)`: pure transformation, testable without network

Otherwise tests become impossible without hitting the web.

### Tests per source

For each source, a snapshot of the payload (HTML/JSON) committed under `tests/fixtures/`. No live tests that hit the network (flaky). Re-snapshot when the format changes.

```python
# tests/services/events/sources/test_<source>.py
import pytest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "<source>_response.{html,json}"

@pytest.fixture
def payload():
    return FIXTURE.read_text()

def test_parser_extracts_all_events(payload):
    source = SourceClass()
    events = source._parse(payload)
    assert len(events) >= source.expected_min_events
    assert all(e.scheduled_at.tzinfo is not None for e in events)
    # ... assertions specific to the event type
```

---

## 2. Isolating scheduler (deliverable 2)

File: `src/api/orchestration/events/scheduler.py`

```python
import asyncio
import logging
import random
from .sources.base import EventSource, RawEvent
from .deduplicator import EventDeduplicator
from .repository import EventsRepository

logger = logging.getLogger(__name__)

class EventsScheduler:
    """
    Orchestrates N sources in parallel, isolates failures, dedupes, persists.
    
    Invariants:
    - 1 source crashing → log warning, the others keep running
    - 1 source timing out → capped at source.timeout_seconds
    - fewer events returned than expected_min_events → log warning (parser drift)
    """

    def __init__(
        self,
        sources: list[EventSource],
        repository: EventsRepository,
        deduplicator: EventDeduplicator,
        interval_hours: float = 24.0,
        jitter_minutes: float = 10.0,
    ):
        self.sources = sources
        self.repository = repository
        self.deduplicator = deduplicator
        self.interval_hours = interval_hours
        self.jitter_minutes = jitter_minutes
        self._task: asyncio.Task | None = None

    async def run_once(self) -> dict[str, int]:
        """
        Runs one full cycle. Returns a per-source report.
        Used by the loop AND by the tests.
        """
        results = await asyncio.gather(
            *[self._fetch_safely(s) for s in self.sources],
            return_exceptions=False,  # already caught inside _fetch_safely
        )
        
        all_events: list[RawEvent] = []
        report: dict[str, int] = {}
        for source, events in zip(self.sources, results):
            report[source.name] = len(events)
            all_events.extend(events)
        
        deduped = self.deduplicator.dedupe(all_events)
        inserted = await self.repository.upsert_many(deduped)
        report["_inserted"] = inserted
        report["_deduped_count"] = len(deduped)
        
        logger.info(f"events sync cycle complete: {report}")
        return report

    async def _fetch_safely(self, source: EventSource) -> list[RawEvent]:
        """Wraps fetch(): timeout, exception isolation, min_events validation."""
        try:
            events = await asyncio.wait_for(
                source.fetch(),
                timeout=source.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(f"source {source.name} timed out after {source.timeout_seconds}s")
            return []
        except Exception as e:
            logger.warning(f"source {source.name} failed: {type(e).__name__}: {e}")
            return []
        
        if len(events) < source.expected_min_events:
            logger.warning(
                f"source {source.name} returned {len(events)} events "
                f"(expected >= {source.expected_min_events}) — possible parser drift"
            )
        return events

    async def start(self):
        """Starts the infinite loop. To be called from the FastAPI lifespan."""
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        await asyncio.sleep(30)  # let the API become ready
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.exception(f"scheduler cycle crashed (should not happen): {e}")
            
            jitter = random.uniform(-self.jitter_minutes, self.jitter_minutes) * 60
            sleep_seconds = self.interval_hours * 3600 + jitter
            await asyncio.sleep(sleep_seconds)
```

### Scheduler tests (with FakeSource, no network)

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

class FakeSource:
    def __init__(self, name, events=None, raises=None, sleep=0):
        self.name = name
        self.expected_min_events = 1
        self.timeout_seconds = 5.0
        self._events, self._raises, self._sleep = events or [], raises, sleep

    async def fetch(self):
        if self._sleep: await asyncio.sleep(self._sleep)
        if self._raises: raise self._raises
        return self._events

@pytest.mark.asyncio
async def test_scheduler_isolates_failing_source():
    good = FakeSource("good", events=[_make_event("FOMC", "US")])
    bad = FakeSource("bad", raises=RuntimeError("boom"))
    repo = AsyncMock(); repo.upsert_many.return_value = 1
    dedup = MagicMock(); dedup.dedupe = lambda x: x
    
    scheduler = EventsScheduler([good, bad], repo, dedup)
    report = await scheduler.run_once()
    
    assert report["good"] == 1
    assert report["bad"] == 0
    assert report["_inserted"] == 1

@pytest.mark.asyncio
async def test_scheduler_caps_slow_source():
    slow = FakeSource("slow", sleep=10); slow.timeout_seconds = 0.1
    fast = FakeSource("fast", events=[_make_event("CPI", "US")])
    repo = AsyncMock(); repo.upsert_many.return_value = 1
    dedup = MagicMock(); dedup.dedupe = lambda x: x
    
    scheduler = EventsScheduler([slow, fast], repo, dedup)
    report = await scheduler.run_once()
    
    assert report["slow"] == 0
    assert report["fast"] == 1
```

---

## 3. Idempotent hash (deliverable 3)

### Hash convention

Identity of an event = `(event_type, region, scheduled_at_truncated_to_minute)`.

Why truncate to the minute:
- one source may return `14:30:00`, another `14:30:15` for the same release
- the seconds carry no semantic value (central banks publish to the minute)
- avoids duplicates when 2 sources overlap (FRED + BLS report the same CPI)

```python
# src/api/orchestration/events/hashing.py
import hashlib
from .sources.base import RawEvent

def event_hash(e: RawEvent) -> str:
    """SHA-256 hex truncated to 16 chars. Identity = (type, region, minute)."""
    minute = e.scheduled_at.replace(second=0, microsecond=0).isoformat()
    key = f"{e.event_type}|{e.region}|{minute}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

### Deduplicator

```python
# src/api/orchestration/events/deduplicator.py
from .sources.base import RawEvent
from .hashing import event_hash

class EventDeduplicator:
    """
    INTRA-cycle dedupe (before insert).
    If 2 sources return the same CPI, keep only 1 (arrival order).
    INTER-cycle dedupe is handled by the UNIQUE constraint in the DB.
    """
    def dedupe(self, events: list[RawEvent]) -> list[tuple[str, RawEvent]]:
        seen: dict[str, RawEvent] = {}
        for e in events:
            h = event_hash(e)
            if h not in seen:
                seen[h] = e
        return [(h, e) for h, e in seen.items()]
```

### DB migration

```sql
-- migrations/00X_events_hash.sql
ALTER TABLE events ADD COLUMN event_hash VARCHAR(16);

UPDATE events SET event_hash = SUBSTRING(
    ENCODE(DIGEST(
        event_type || '|' || region || '|' ||
        TO_CHAR(scheduled_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS'),
        'sha256'
    ), 'hex'), 1, 16
);

ALTER TABLE events ALTER COLUMN event_hash SET NOT NULL;
ALTER TABLE events ADD CONSTRAINT events_hash_unique UNIQUE (event_hash);
CREATE INDEX idx_events_hash ON events(event_hash);
```

### Repository

```python
# src/api/orchestration/events/repository.py
from sqlalchemy import text
from .sources.base import RawEvent

class EventsRepository:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    async def upsert_many(self, hashed_events: list[tuple[str, RawEvent]]) -> int:
        if not hashed_events:
            return 0
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    INSERT INTO events 
                        (event_hash, event_type, region, impact, scheduled_at, description)
                    VALUES 
                        (:event_hash, :event_type, :region, :impact, :scheduled_at, :description)
                    ON CONFLICT (event_hash) DO NOTHING
                """),
                [
                    {
                        "event_hash": h, "event_type": e.event_type,
                        "region": e.region, "impact": e.impact,
                        "scheduled_at": e.scheduled_at, "description": e.description,
                    }
                    for h, e in hashed_events
                ],
            )
            await session.commit()
            return result.rowcount
```

---

## 4. Exhaustive list of sources to implement

### 2-tier architecture (revised)

The major asymmetry: the **FRED API** (St. Louis Fed) exposes a `/fred/releases/dates` endpoint aggregating the release dates of ALL official US statistics (BLS, BEA, Fed, Census, Treasury) as JSON, free, with a key obtained in 30s. It is the only aggregator you can reasonably use as a primary source — because it is operated by the Fed itself, not by a commercial actor.

```
┌─────────────────────────────────────────────────────────────┐
│ TIER 1 — Primary sources (5 sources, cover 95%+)            │
│  [US]  FREDSource          → CPI, NFP, PCE, GDP, PPI, etc.  │
│  [EU]  ECBSource           → Governing Council meetings     │
│  [GB]  BoESource           → MPC decisions                  │
│  [US]  FOMCSource          → meetings + minutes             │
│  [EU]  EurostatSource      → CPI/HICP, euro area GDP        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ TIER 2 — Secondary sources (2 sources, redundancy + GB)     │
│  [GB]  ONSSource           → UK CPI, GDP, employment        │
│  [US]  BLSSource           → fallback if FRED key down      │
└─────────────────────────────────────────────────────────────┘
```

### Per-source detail

#### `FREDSource` — covers the whole US block in one shot

| Field | Value |
|---|---|
| URL | `https://api.stlouisfed.org/fred/releases/dates` |
| Format | JSON |
| Auth | API key (free, 30s to obtain) |
| Coverage | all official US `release_id`s (CPI, NFP, PCE, GDP, FOMC, etc.) |
| Signup endpoint | `https://fredaccount.stlouisfed.org/apikeys` |
| Env var | `FRED_API_KEY` |

**Strategy**: call the endpoint with `realtime_end=today+180d` and `include_release_dates_with_no_data=true` to retrieve future dates. Then filter on the `release_id`s that map to high-impact events.

```python
# Whitelist of FRED release_ids to keep
# (verify via GET /fred/releases?api_key=KEY&file_type=json&order_by=popularity)
FRED_HIGH_IMPACT_RELEASES = {
    10:  ("CPI", "high"),           # Consumer Price Index (BLS)
    50:  ("NFP", "high"),           # Employment Situation (BLS) — contains NFP
    21:  ("M2", "medium"),          # H.6 Money Stock (Fed)
    53:  ("GDP", "high"),           # Gross Domestic Product (BEA)
    151: ("PCE", "high"),           # Personal Income and Outlays (BEA)
    46:  ("PPI", "medium"),         # Producer Price Index (BLS)
    101: ("FOMC", "high"),          # H.15 Selected Interest Rates (Fed)
    175: ("RetailSales", "medium"), # Advance Monthly Retail Sales (Census)
    82:  ("ISM_Mfg", "medium"),     # ISM Manufacturing
}
```

```python
class FREDSource(EventSource):
    name = "FRED"
    expected_min_events = 10  # ~10 high-impact releases over 6 months minimum
    
    BASE_URL = "https://api.stlouisfed.org/fred"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
    
    async def fetch(self) -> list[RawEvent]:
        from datetime import date, timedelta
        end_date = (date.today() + timedelta(days=180)).isoformat()
        
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(
                f"{self.BASE_URL}/releases/dates",
                params={
                    "api_key": self.api_key,
                    "file_type": "json",
                    "realtime_end": end_date,
                    "include_release_dates_with_no_data": "true",
                    "limit": 10000,
                    "sort_order": "asc",
                },
            )
            resp.raise_for_status()
        return self._parse(resp.json())
    
    def _parse(self, payload: dict) -> list[RawEvent]:
        events = []
        for r in payload.get("release_dates", []):
            release_id = r["release_id"]
            if release_id not in FRED_HIGH_IMPACT_RELEASES:
                continue
            event_type, impact = FRED_HIGH_IMPACT_RELEASES[release_id]
            # FRED gives the date without a time; US releases at 8:30 ET (12:30/13:30 UTC depending on DST)
            scheduled_at = self._localize_us_release(r["date"], event_type)
            events.append(RawEvent(
                event_type=event_type, region="US", impact=impact,
                scheduled_at=scheduled_at,
                description=r.get("release_name", ""),
                source_name=self.name,
            ))
        return events
    
    def _localize_us_release(self, date_str: str, event_type: str) -> datetime:
        """8:30 ET for most BLS/BEA releases, 14:00 ET for FOMC."""
        # implement the mapping event_type → local time → UTC tz-aware
        # use zoneinfo.ZoneInfo("America/New_York") to handle DST
        ...
```

#### `ECBSource` — EU Governing Council

| Field | Value |
|---|---|
| URL | `https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html` |
| Format | HTML |
| Auth | none |
| Coverage | 8 monetary policy meetings/year + non-MP meetings |
| Time | 14:15 CET for MP decision, 14:45 CET for press conf |

Note: the weekly calendar `https://www.ecb.europa.eu/press/calendars/weekly/html/index.en.html` also provides precise times for the current week. Useful as a complement.

#### `BoESource` — UK MPC

| Field | Value |
|---|---|
| URL | `https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates` |
| Format | HTML |
| Auth | none |
| Coverage | 8 MPC meetings/year, dates confirmed 6+ months in advance |
| Time | 12:00 UK time (announcement) |

2026 dates confirmed by the BoE: February 5, March 19, April 30, June 18, July 30, September 17, November 5, December 17.

#### `FOMCSource` — Fed meetings (complement to FRED)

| Field | Value |
|---|---|
| URL | `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm` |
| Format | HTML |
| Auth | none |
| Coverage | 8 meetings + minutes releases (3 weeks after) |

**Why in addition to FRED**: FRED exposes the "H.15 Selected Interest Rates" release which comes out on FOMC day, but not the FOMC meeting itself as an explicit, distinct event. The minutes (3 weeks after) must come from here.

#### `EurostatSource` — euro area statistics

| Field | Value |
|---|---|
| URL | `https://ec.europa.eu/eurostat/news/release-calendar` |
| Format | HTML / RSS |
| Auth | none |
| Coverage | HICP flash + final, GDP flash + final, employment |

Note: Eurostat also has a REST API endpoint at `https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/` but for the publication calendar, HTML/RSS is enough.

#### `ONSSource` — UK statistics

| Field | Value |
|---|---|
| URL | `https://www.ons.gov.uk/releasecalendar` |
| Format | HTML |
| Auth | none |
| Coverage | UK CPI, GDP, employment, retail sales |
| Time | 7:00 UK time for most |

#### `BLSSource` — fallback if FRED is unavailable

| Field | Value |
|---|---|
| URL | `https://www.bls.gov/schedule/2026/home.htm` (and `/2027/home.htm`) |
| Format | HTML |
| Auth | none |
| Coverage | redundant with FRED, but with no dependency on the key |
| Time | 8:30 ET for most releases |

Implement it but leave it **disabled by default** in the list of active sources — activate it only if FRED fails regularly (30-day monitoring in prod).

### Recap table: coverage × event_type

| event_type | Region | Primary source | Backup source |
|---|---|---|---|
| CPI | US | FRED | BLS |
| NFP | US | FRED | BLS |
| PCE | US | FRED | — |
| GDP | US | FRED | — |
| PPI | US | FRED | BLS |
| FOMC | US | FOMCSource | FRED (H.15 release) |
| Retail Sales | US | FRED | — |
| ISM Mfg | US | FRED | — |
| ECB rate | EU | ECBSource | — |
| HICP | EU | EurostatSource | — |
| EU GDP | EU | EurostatSource | — |
| BoE rate | GB | BoESource | — |
| UK CPI | GB | ONSSource | — |
| UK GDP | GB | ONSSource | — |

**Estimated coverage for EUR/USD high-impact**: ≥98% with FRED + ECB + BoE + FOMC + Eurostat (5 sources). ONS and BLS are optional backups.

---

## 5. Final wiring in the API

`src/api/main.py`:

```python
import os
from contextlib import asynccontextmanager
from .services.events.scheduler import EventsScheduler
from .services.events.sources.fred import FREDSource
from .services.events.sources.ecb import ECBSource
from .services.events.sources.boe import BoESource
from .services.events.sources.fomc import FOMCSource
from .services.events.sources.eurostat import EurostatSource
from .services.events.sources.ons import ONSSource
from .services.events.deduplicator import EventDeduplicator
from .services.events.repository import EventsRepository

@asynccontextmanager
async def lifespan(app):
    sources = [
        FREDSource(api_key=os.environ["FRED_API_KEY"]),
        ECBSource(),
        BoESource(),
        FOMCSource(),
        EurostatSource(),
        ONSSource(),
    ]
    scheduler = EventsScheduler(
        sources=sources,
        repository=EventsRepository(session_factory),
        deduplicator=EventDeduplicator(),
    )
    await scheduler.start()
    yield
    await scheduler.stop()
```

`docker-compose.yml`: add `FRED_API_KEY=${FRED_API_KEY}` to the api's env vars.

`.env`: `FRED_API_KEY=<key>` (obtained at https://fredaccount.stlouisfed.org/apikeys).

Delete the old `_events_sync_loop` in `main.py` and the old `events_fetcher.py`.

---

## 6. Strict delivery order

```
PR 1 : Source pattern + FREDSource + tests + JSON fixture
       (FRED first because it covers 60% of the events in 1 source)

PR 2 : EventsScheduler + EventDeduplicator + hashing + scheduler tests
       (with FakeSource only, independent of PR 1)

PR 3 : event_hash DB migration + EventsRepository + idempotency tests

PR 4 : lifespan wiring + removal of the old fetcher
       + ECBSource + BoESource + FOMCSource with their HTML fixtures

PR 5 : EurostatSource + ONSSource (full EU/GB coverage)

PR 6 (optional) : BLSSource disabled by default, to activate on FRED drift
```

Do not merge PR 4 before PR 1-3 are green in CI.

---

## 7. Validation checklist

- [ ] `pytest tests/services/events/` passes at 100%
- [ ] coverage ≥ 90% on `services/events/`
- [ ] no test hits the network (all via fixtures)
- [ ] adding a new source = 1 file in `sources/`, 1 line in `main.py`
- [ ] `docker compose up` → logs show `events sync cycle complete: {...}` after 30s
- [ ] `events` table contains ≥30 future events over the next 6 months after the 1st run
- [ ] re-run 24h later → `_inserted: 0` if nothing moved on the source side
- [ ] kill one source (change its URL to localhost:1) → the others keep producing
- [ ] invalid FRED key → FRED logs a warning, other sources keep working

---

## 8. User setup (one-time)

1. **FRED API key** (covers all US sources at once):
   - go to `https://fredaccount.stlouisfed.org/apikeys`
   - email + password (~30s)
   - copy the key into `.env`: `FRED_API_KEY=...`
2. `docker compose up -d --force-recreate api`
3. Check logs: `docker compose logs api | grep "events sync"`

That's it. No intervention ever again.

---

## 9. Out of scope (do not implement here)

- Trading Economics API: dropped from the design, no longer needed with FRED
- ForexFactory scraping: dropped from the design, Cloudflare fragility
- Discord/Slack webhook when a source drifts: nice-to-have, after v1
- Admin UI to view source status: not before we have signal on the drifts
- Secondary central bank events (BoJ, SNB, RBA): add when the trading system's scope extends beyond EUR/USD

---

## 10. Answer to the question "will this be a professional-grade service?"

Yes, provided the 3 fundamentals (Source pattern + isolating scheduler + idempotent hash) are delivered as specified. The proposed design eliminates:

- the dependency on a fragile commercial aggregator (TE/FF)
- single points of failure (1 source down ≠ pipeline down)
- manual intervention (24h auto-refresh)
- duplicates (hash + UNIQUE constraint)
- flaky tests (committed fixtures)

What remains **unhandled** by this implementation and will need consideration later if the system grows in importance:
- active monitoring (alert if `_inserted` stays at 0 for several days in a row)
- versioning of the HTML fixtures (a site changing its format silently breaks the parser until `expected_min_events` triggers)
- historical backfill (the spec covers future events, not the archive)
- rescheduling handling (a BLS release shifted by 2 days = 2 different hashes, the old one stays in the DB; a "supersede" logic will be needed)

These points belong to v2, not v1.
