"""Tests for /api/v1/vol/* — surface (Redis), surface/at (PG), term-structure, smile."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_db_session
from api.main import create_app

_TS = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
_TS_ISO = _TS.isoformat().replace("+00:00", "Z")

# Mimics what VolEngine writes to Redis (compact) and what R2 writer stores in PG (full).
_REDIS_PAYLOAD = {
    "symbol": "EURUSD",
    "timestamp": _TS_ISO,
    "surface": {
        "1M": {"dte": 30, "sigma_atm_pct": 7.5, "sigma_fair_pct": 7.4, "signal": "CHEAP"},
        "3M": {"dte": 90, "sigma_atm_pct": 8.0, "sigma_fair_pct": 7.9, "signal": "FAIR"},
    },
}

_PG_FULL_PILLAR_1M = {
    "dte": 30, "sigma_ATM_pct": 7.5, "sigma_fair_pct": 7.4,
    "iv_10dp_pct": 8.2, "iv_25dp_pct": 7.7,
    "iv_25dc_pct": 7.6, "iv_10dc_pct": 8.1,
    "strike_10dp": 1.070, "strike_25dp": 1.080, "strike_atm": 1.085,
    "strike_25dc": 1.090, "strike_10dc": 1.100,
}


@pytest.fixture
def fake_redis():
    r = AsyncMock()
    r.aclose = AsyncMock()
    r.get = AsyncMock(return_value=json.dumps(_REDIS_PAYLOAD))
    return r


@pytest.fixture
def client(fake_redis):
    """TestClient with mocked Redis and DB session override."""
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock()

    def _stub_row(surface_data):
        return SimpleNamespace(
            underlying="EURUSD", timestamp=_TS, surface_data=surface_data
        )

    client._stub_row = _stub_row  # exposed for tests to set the returned row
    client._fake_session = fake_session

    async def _fake_db():
        yield fake_session

    with patch("api.main.aioredis.from_url", return_value=fake_redis):
        app = create_app()
        app.dependency_overrides[get_db_session] = _fake_db
        with TestClient(app) as c:
            c._fake_session = fake_session  # type: ignore[attr-defined]
            c._stub_row = _stub_row          # type: ignore[attr-defined]
            yield c


def _set_pg_row(client: TestClient, row):
    """Make the fake db.execute().scalar_one_or_none() return ``row``."""
    result = AsyncMock()
    result.scalar_one_or_none = lambda: row
    client._fake_session.execute = AsyncMock(return_value=result)


@pytest.mark.unit
class TestSurfaceRoute:
    def test_returns_redis_payload(self, client):
        r = client.get("/api/v1/vol/surface?symbol=EURUSD")
        assert r.status_code == 200
        body = r.json()
        assert body["symbol"] == "EURUSD"
        assert set(body["surface"]) == {"1M", "3M"}

    def test_404_when_redis_empty(self, client, fake_redis):
        fake_redis.get = AsyncMock(return_value=None)
        r = client.get("/api/v1/vol/surface?symbol=EURUSD")
        assert r.status_code == 404


@pytest.mark.unit
class TestSurfaceAtRoute:
    def test_returns_pg_row(self, client):
        _set_pg_row(client, client._stub_row({"1M": {"dte": 30, "sigma_ATM_pct": 7.5}}))
        r = client.get(f"/api/v1/vol/surface/at/{_TS_ISO}?symbol=EURUSD")
        assert r.status_code == 200
        assert r.json()["surface"]["1M"]["sigma_ATM_pct"] == 7.5

    def test_404_when_not_in_pg(self, client):
        _set_pg_row(client, None)
        r = client.get(f"/api/v1/vol/surface/at/{_TS_ISO}?symbol=EURUSD")
        assert r.status_code == 404


@pytest.mark.unit
class TestTermStructureRoute:
    def test_derives_pillars_from_redis_surface(self, client):
        r = client.get("/api/v1/vol/term-structure?symbol=EURUSD")
        body = r.json()
        assert r.status_code == 200
        tenors = [p["tenor"] for p in body["pillars"]]
        assert tenors == ["1M", "3M"]
        assert body["pillars"][0]["sigma_atm_pct"] == 7.5


@pytest.mark.unit
class TestSmileRoute:
    def test_returns_five_points(self, client):
        _set_pg_row(client, client._stub_row({"1M": _PG_FULL_PILLAR_1M}))
        r = client.get("/api/v1/vol/smile/1M?symbol=EURUSD")
        body = r.json()
        assert r.status_code == 200
        labels = [p["delta_label"] for p in body["points"]]
        assert labels == ["10P", "25P", "ATM", "25C", "10C"]
        assert body["dte"] == 30

    def test_404_when_tenor_absent(self, client):
        _set_pg_row(client, client._stub_row({"1M": _PG_FULL_PILLAR_1M}))
        r = client.get("/api/v1/vol/smile/6M?symbol=EURUSD")
        assert r.status_code == 404
