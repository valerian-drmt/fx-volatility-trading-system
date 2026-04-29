"""Tests for /api/v1/{positions,risk,pnl-curve,history}."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_db_session
from api.main import create_app

_TS = datetime(2026, 4, 20, 10, 0, 0, tzinfo=UTC)
_TS_ISO = _TS.isoformat().replace("+00:00", "Z")


def _pos_row(pid: int = 42) -> SimpleNamespace:
    """Stub PG row matching the Position ORM shape (from_attributes=True)."""
    return SimpleNamespace(
        id=pid, symbol="EUR.USD", instrument_type="OPTION", side="BUY",
        quantity=Decimal("1"), strike=Decimal("1.08000"),
        maturity=date(2026, 5, 15), option_type="CALL",
        entry_price=Decimal("0.00500"), entry_timestamp=_TS, status="OPEN",
    )


def _snap_row() -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=_TS, spot=Decimal("1.0857"), iv=Decimal("7.40"),
        delta_usd=Decimal("500"), vega_usd=Decimal("120"), gamma_usd=Decimal("800"),
        theta_usd=Decimal("-15"), pnl_usd=Decimal("25"),
    )


@pytest.fixture
def fake_redis():
    r = AsyncMock()
    r.aclose = AsyncMock()
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture
def client(fake_redis):
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock()
    fake_session.get = AsyncMock(return_value=None)

    async def _fake_db():
        yield fake_session

    with patch("api.main.aioredis.from_url", return_value=fake_redis):
        app = create_app()
        app.dependency_overrides[get_db_session] = _fake_db
        with TestClient(app) as c:
            c._fake_session = fake_session  # type: ignore[attr-defined]
            yield c


def _stub_execute(client: TestClient, rows):
    """Make db.execute() return an object whose scalars().all() yields rows."""
    result = AsyncMock()
    result.scalars = lambda: SimpleNamespace(all=lambda: rows)
    client._fake_session.execute = AsyncMock(return_value=result)


@pytest.mark.unit
class TestPositionsRoutes:
    def test_list_returns_position_rows(self, client):
        _stub_execute(client, [_pos_row(42), _pos_row(43)])
        r = client.get("/api/v1/positions")
        body = r.json()
        assert r.status_code == 200
        assert len(body) == 2
        assert body[0]["id"] == 42 and body[0]["status"] == "OPEN"

    def test_list_rejects_invalid_status_filter(self, client):
        r = client.get("/api/v1/positions?status=WEIRD")
        assert r.status_code == 422

    def test_get_by_id_200(self, client):
        client._fake_session.get = AsyncMock(return_value=_pos_row(42))
        r = client.get("/api/v1/positions/42")
        assert r.status_code == 200
        assert r.json()["id"] == 42

    def test_get_by_id_404(self, client):
        client._fake_session.get = AsyncMock(return_value=None)
        r = client.get("/api/v1/positions/999")
        assert r.status_code == 404


@pytest.mark.unit
class TestRiskAndPnlRoutes:
    def test_risk_reads_latest_greeks_from_redis(self, client, fake_redis):
        fake_redis.get = AsyncMock(return_value=json.dumps({
            "timestamp": _TS_ISO,
            "greeks": {"delta_net": 1200, "vega_net": 500},
        }))
        r = client.get("/api/v1/risk")
        body = r.json()
        assert r.status_code == 200
        assert body["greeks"]["delta_net"] == 1200

    def test_risk_404_when_redis_empty(self, client, fake_redis):
        fake_redis.get = AsyncMock(return_value=None)
        assert client.get("/api/v1/risk").status_code == 404

    def test_pnl_curve_reads_redis(self, client, fake_redis):
        fake_redis.get = AsyncMock(return_value=json.dumps({
            "timestamp": _TS_ISO,
            "curve": {"spots": [1.08, 1.09], "pnls": [0, 100]},
        }))
        r = client.get("/api/v1/pnl-curve")
        body = r.json()
        assert r.status_code == 200
        assert body["curve"]["spots"] == [1.08, 1.09]


@pytest.mark.unit
class TestHistoryRoute:
    def test_returns_snapshots_for_existing_position(self, client):
        client._fake_session.get = AsyncMock(return_value=_pos_row(42))
        _stub_execute(client, [_snap_row(), _snap_row()])
        r = client.get("/api/v1/history?position_id=42")
        body = r.json()
        assert r.status_code == 200
        assert body["position_id"] == 42
        assert len(body["snapshots"]) == 2

    def test_404_when_position_absent(self, client):
        client._fake_session.get = AsyncMock(return_value=None)
        r = client.get("/api/v1/history?position_id=999")
        assert r.status_code == 404
