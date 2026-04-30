# Events pipeline — refactor spec

Cible : remplacer la cascade actuelle (`TradingEconomics → ForexFactory`) par un agrégateur multi-sources orthogonales, testable, isolé par source, idempotent.

À l'issue de cette implémentation : **service de récupération d'events économiques de qualité production**, sans dépendance à un agrégateur tiers commercial, sans intervention manuelle, refresh quotidien automatique.

Trois éléments fondamentaux à livrer, dans l'ordre, puis 7 sources concrètes branchées dessus.

---

## 0. Système

```
┌──────────────────────────────────────────────────────────────────┐
│                        EventsScheduler                           │
│  • boucle async (24h interval, jitter ±10min)                    │
│  • exécute chaque Source en parallèle (asyncio.gather)           │
│  • isole les échecs : 1 source down ≠ pipeline down              │
│  • collecte → dédup par hash → upsert idempotent                 │
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

Contraintes :

- une source qui timeout/crash ne bloque pas les autres
- un re-run quotidien doit être un no-op si rien n'a changé
- ajouter une nouvelle source = créer 1 classe, 0 modification du scheduler
- chaque source est testable en isolation (mock HTTP)

---

## 1. Pattern `Source` (livraison 1)

### Interface

Fichier : `src/api/services/events/sources/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Region = Literal["US", "EU", "GB"]
Impact = Literal["high", "medium", "low"]

@dataclass(frozen=True)
class RawEvent:
    """Événement tel que renvoyé par une source, avant dédup/persist."""
    event_type: str          # "FOMC", "NFP", "CPI", ...
    region: Region
    impact: Impact
    scheduled_at: datetime   # UTC, tz-aware
    description: str
    source_name: str         # "FRED", "ECB", ... pour debug

class EventSource(ABC):
    """Contrat pour toute source d'événements économiques."""

    name: str                       # identifiant unique, utilisé dans les logs
    timeout_seconds: float = 10.0   # cap dur, pas de source qui traîne
    expected_min_events: int = 1    # alerte si fetch retourne moins que ça

    @abstractmethod
    async def fetch(self) -> list[RawEvent]:
        """
        Récupère et parse les events. 
        Doit lever une exception en cas d'échec — le scheduler la catch.
        Ne pas swallow les erreurs ici.
        """
        ...
```

### Règle de séparation I/O / parsing

Chaque source DOIT séparer :
- `fetch()` : I/O réseau uniquement, retourne le payload brut (HTML/JSON/XML)
- `_parse(payload)` : transformation pure, testable sans réseau

Sinon les tests deviennent impossibles à faire sans hit le web.

### Tests par source

Pour chaque source, un snapshot du payload (HTML/JSON) committé dans `tests/fixtures/`. Pas de test live qui hit le réseau (flaky). Re-snapshot quand le format change.

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
    # ... assertions spécifiques au type d'event
```

---

## 2. Scheduler isolant (livraison 2)

Fichier : `src/api/services/events/scheduler.py`

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
    Orchestre N sources en parallèle, isole les échecs, dédup, persist.
    
    Invariants :
    - 1 source qui crash → log warning, les autres continuent
    - 1 source qui timeout → cap à source.timeout_seconds
    - 0 event retourné < expected_min_events → log warning (drift parser)
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
        Exécute un cycle complet. Retourne un report par source.
        Utilisé par la boucle ET par les tests.
        """
        results = await asyncio.gather(
            *[self._fetch_safely(s) for s in self.sources],
            return_exceptions=False,  # déjà catch dans _fetch_safely
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
        """Wrap fetch() : timeout, exception isolation, validation min_events."""
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
        """Démarre la boucle infinie. À appeler depuis lifespan FastAPI."""
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self):
        await asyncio.sleep(30)  # laisse l'API ready
        while True:
            try:
                await self.run_once()
            except Exception as e:
                logger.exception(f"scheduler cycle crashed (should not happen): {e}")
            
            jitter = random.uniform(-self.jitter_minutes, self.jitter_minutes) * 60
            sleep_seconds = self.interval_hours * 3600 + jitter
            await asyncio.sleep(sleep_seconds)
```

### Tests scheduler (avec FakeSource, pas de réseau)

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

## 3. Hash idempotent (livraison 3)

### Convention de hash

Identité d'un event = `(event_type, region, scheduled_at_truncated_to_minute)`.

Pourquoi tronquer à la minute :
- une source peut renvoyer `14:30:00`, une autre `14:30:15` pour le même release
- la seconde n'a aucune valeur sémantique (les BC publient à la minute près)
- évite les doublons quand 2 sources se chevauchent (FRED + BLS donnent le même CPI)

```python
# src/api/services/events/hashing.py
import hashlib
from .sources.base import RawEvent

def event_hash(e: RawEvent) -> str:
    """SHA-256 hex tronqué à 16 chars. Identité = (type, region, minute)."""
    minute = e.scheduled_at.replace(second=0, microsecond=0).isoformat()
    key = f"{e.event_type}|{e.region}|{minute}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
```

### Deduplicator

```python
# src/api/services/events/deduplicator.py
from .sources.base import RawEvent
from .hashing import event_hash

class EventDeduplicator:
    """
    Dédup INTRA-cycle (avant insert).
    Si 2 sources renvoient le même CPI, on garde 1 seul (ordre d'arrivée).
    Le dédup INTER-cycle est géré par la contrainte UNIQUE en DB.
    """
    def dedupe(self, events: list[RawEvent]) -> list[tuple[str, RawEvent]]:
        seen: dict[str, RawEvent] = {}
        for e in events:
            h = event_hash(e)
            if h not in seen:
                seen[h] = e
        return [(h, e) for h, e in seen.items()]
```

### Migration DB

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
# src/api/services/events/repository.py
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

## 4. Liste exhaustive des sources à implémenter

### Architecture en 2 tiers (révisée)

L'asymétrie majeure : **FRED API** (St. Louis Fed) expose un endpoint `/fred/releases/dates` qui agrège les dates de release de TOUTES les statistiques US officielles (BLS, BEA, Fed, Census, Treasury) en JSON, gratuit, key obtenue en 30s. C'est le seul agrégateur que tu peux raisonnablement utiliser comme source primaire — parce qu'il est opéré par la Fed elle-même, pas par un acteur commercial.

```
┌─────────────────────────────────────────────────────────────┐
│ TIER 1 — Sources primaires (5 sources, couvrent 95%+)       │
│  [US]  FREDSource          → CPI, NFP, PCE, GDP, PPI, etc.  │
│  [EU]  ECBSource           → Governing Council meetings     │
│  [GB]  BoESource           → MPC decisions                  │
│  [US]  FOMCSource          → meetings + minutes             │
│  [EU]  EurostatSource      → CPI/HICP, GDP zone euro        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ TIER 2 — Sources secondaires (2 sources, redondance + GB)   │
│  [GB]  ONSSource           → UK CPI, GDP, employment        │
│  [US]  BLSSource           → fallback si FRED key down      │
└─────────────────────────────────────────────────────────────┘
```

### Détail par source

#### `FREDSource` — couvre tout le bloc US d'un seul coup

| Champ | Valeur |
|---|---|
| URL | `https://api.stlouisfed.org/fred/releases/dates` |
| Format | JSON |
| Auth | API key (gratuite, 30s à obtenir) |
| Couverture | tous les `release_id` US officiels (CPI, NFP, PCE, GDP, FOMC, etc.) |
| Endpoint signup | `https://fredaccount.stlouisfed.org/apikeys` |
| Env var | `FRED_API_KEY` |

**Stratégie** : appeler l'endpoint avec `realtime_end=today+180d` et `include_release_dates_with_no_data=true` pour récupérer les dates futures. Filtrer ensuite sur les `release_id` qui correspondent aux events high-impact.

```python
# Whitelist des release_id FRED à garder
# (vérifier via GET /fred/releases?api_key=KEY&file_type=json&order_by=popularity)
FRED_HIGH_IMPACT_RELEASES = {
    10:  ("CPI", "high"),           # Consumer Price Index (BLS)
    50:  ("NFP", "high"),           # Employment Situation (BLS) — contient NFP
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
    expected_min_events = 10  # ~10 high-impact releases sur 6 mois minimum
    
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
            # FRED donne la date sans heure ; releases US à 8:30 ET (12:30/13:30 UTC selon DST)
            scheduled_at = self._localize_us_release(r["date"], event_type)
            events.append(RawEvent(
                event_type=event_type, region="US", impact=impact,
                scheduled_at=scheduled_at,
                description=r.get("release_name", ""),
                source_name=self.name,
            ))
        return events
    
    def _localize_us_release(self, date_str: str, event_type: str) -> datetime:
        """8:30 ET pour la plupart des releases BLS/BEA, 14:00 ET pour FOMC."""
        # implémenter le mapping event_type → heure locale → UTC tz-aware
        # utiliser zoneinfo.ZoneInfo("America/New_York") pour gérer DST
        ...
```

#### `ECBSource` — Governing Council EU

| Champ | Valeur |
|---|---|
| URL | `https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html` |
| Format | HTML |
| Auth | aucune |
| Couverture | 8 meetings monetary policy/an + non-MP meetings |
| Heure | 14:15 CET pour décision MP, 14:45 CET pour press conf |

Note : le calendrier weekly `https://www.ecb.europa.eu/press/calendars/weekly/html/index.en.html` fournit aussi les heures précises pour la semaine courante. Utile en complément.

#### `BoESource` — MPC UK

| Champ | Valeur |
|---|---|
| URL | `https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates` |
| Format | HTML |
| Auth | aucune |
| Couverture | 8 MPC meetings/an, dates confirmées 6+ mois en avance |
| Heure | 12:00 UK time (announcement) |

Dates 2026 confirmées par BoE : 5 février, 19 mars, 30 avril, 18 juin, 30 juillet, 17 septembre, 5 novembre, 17 décembre.

#### `FOMCSource` — meetings Fed (complément à FRED)

| Champ | Valeur |
|---|---|
| URL | `https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm` |
| Format | HTML |
| Auth | aucune |
| Couverture | 8 meetings + minutes releases (3 semaines après) |

**Pourquoi en plus de FRED** : FRED expose la release "H.15 Selected Interest Rates" qui sort le jour du FOMC, mais pas explicitement le meeting FOMC lui-même comme event distinct. Les minutes (3 semaines après) doivent venir d'ici.

#### `EurostatSource` — statistiques zone euro

| Champ | Valeur |
|---|---|
| URL | `https://ec.europa.eu/eurostat/news/release-calendar` |
| Format | HTML / RSS |
| Auth | aucune |
| Couverture | HICP flash + final, GDP flash + final, employment |

Note : Eurostat a aussi un endpoint API REST sur `https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/` mais pour le calendrier des publications, le HTML/RSS suffit.

#### `ONSSource` — UK statistiques

| Champ | Valeur |
|---|---|
| URL | `https://www.ons.gov.uk/releasecalendar` |
| Format | HTML |
| Auth | aucune |
| Couverture | UK CPI, GDP, employment, retail sales |
| Heure | 7:00 UK time pour la plupart |

#### `BLSSource` — fallback si FRED indisponible

| Champ | Valeur |
|---|---|
| URL | `https://www.bls.gov/schedule/2026/home.htm` (et `/2027/home.htm`) |
| Format | HTML |
| Auth | aucune |
| Couverture | redondant avec FRED, mais sans dépendance à la clé |
| Heure | 8:30 ET pour la plupart des releases |

À implémenter mais à laisser **désactivé par défaut** dans la liste des sources actives — l'activer uniquement si FRED échoue régulièrement (monitoring sur 30j en prod).

### Tableau récap : couverture × event_type

| event_type | Région | Source primaire | Source backup |
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

**Couverture estimée pour EUR/USD high-impact** : ≥98% avec FRED + ECB + BoE + FOMC + Eurostat (5 sources). ONS et BLS sont des backups optionnels.

---

## 5. Wiring final dans l'API

`src/api/main.py` :

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

`docker-compose.yml` : ajouter `FRED_API_KEY=${FRED_API_KEY}` dans les env de l'api.

`.env` : `FRED_API_KEY=<clé>` (obtenue sur https://fredaccount.stlouisfed.org/apikeys).

Supprimer l'ancien `_events_sync_loop` dans `main.py` et l'ancien `events_fetcher.py`.

---

## 6. Ordre de livraison strict

```
PR 1 : pattern Source + FREDSource + tests + fixture JSON
       (FRED en premier parce qu'elle couvre 60% des events en 1 source)

PR 2 : EventsScheduler + EventDeduplicator + hashing + tests scheduler
       (avec FakeSource only, indépendant de PR 1)

PR 3 : migration DB event_hash + EventsRepository + tests idempotence

PR 4 : wiring lifespan + suppression ancien fetcher
       + ECBSource + BoESource + FOMCSource avec leurs fixtures HTML

PR 5 : EurostatSource + ONSSource (couverture EU/GB complète)

PR 6 (optionnel) : BLSSource désactivée par défaut, à activer si drift FRED
```

Ne pas merger PR 4 avant que PR 1-3 soient verts en CI.

---

## 7. Checklist de validation

- [ ] `pytest tests/services/events/` passe à 100%
- [ ] couverture ≥ 90% sur `services/events/`
- [ ] aucun test ne hit le réseau (tous via fixtures)
- [ ] ajouter une nouvelle source = 1 fichier dans `sources/`, 1 ligne dans `main.py`
- [ ] `docker compose up` → logs montrent `events sync cycle complete: {...}` après 30s
- [ ] table `events` contient ≥30 events futurs sur les 6 prochains mois après 1er run
- [ ] re-run dans 24h → `_inserted: 0` si rien n'a bougé côté sources
- [ ] kill une source (changer URL en localhost:1) → les autres continuent de produire
- [ ] FRED key invalide → FRED log warning, autres sources fonctionnent

---

## 8. Setup utilisateur (1 fois)

1. **FRED API key** (couvre toutes les sources US d'un coup) :
   - aller sur `https://fredaccount.stlouisfed.org/apikeys`
   - email + password (~30s)
   - copier la clé dans `.env` : `FRED_API_KEY=...`
2. `docker compose up -d --force-recreate api`
3. Vérifier logs : `docker compose logs api | grep "events sync"`

C'est tout. Plus jamais d'intervention.

---

## 9. Hors-scope (ne pas implémenter ici)

- Trading Economics API : éliminée du design, plus nécessaire avec FRED
- ForexFactory scraping : éliminé du design, fragilité Cloudflare
- Webhook Discord/Slack quand une source drift : nice-to-have, après v1
- UI admin pour voir le statut des sources : pas avant qu'on ait du signal sur les drifts
- Events de banques centrales secondaires (BoJ, SNB, RBA) : ajouter quand le scope du système trading s'étend hors EUR/USD

---

## 10. Réponse à la question "ce sera un service pro ?"

Oui, sous condition que les 3 fondamentaux (Source pattern + scheduler isolant + hash idempotent) soient livrés tels que spécifiés. Le design proposé élimine :

- la dépendance à un agrégateur commercial fragile (TE/FF)
- les single points of failure (1 source down ≠ pipeline down)
- l'intervention manuelle (auto-refresh 24h)
- les doublons (hash + UNIQUE constraint)
- les tests flaky (fixtures committées)

Ce qui reste **non-géré** par cette implémentation et qu'il faudra envisager plus tard si le système prend en importance :
- monitoring actif (alerter si `_inserted` reste à 0 plusieurs jours d'affilée)
- versioning des fixtures HTML (un site qui change son format casse silencieusement le parser jusqu'à ce qu'`expected_min_events` déclenche)
- backfill historique (la spec couvre les events futurs, pas l'archive)
- gestion des reschedulings (un BLS qui décale une release de 2 jours = 2 hash différents, le vieux reste en DB ; il faudra une logique de "supersede")

Ces points relèvent du v2, pas du v1.
