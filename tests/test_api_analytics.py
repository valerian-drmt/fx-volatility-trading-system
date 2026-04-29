"""Tests for /api/v1/{signals,vol-history,backtest,system-stats}."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from api.dependencies import get_db_session
from api.main import create_app

_NOW = datetime.now(UTC)
_NOW_ISO = _NOW.isoformat().replace("+00:00", "Z")


def _signal_row(tenor: str = "1M", signal: str = "CHEAP") -> SimpleNamespace:
    return SimpleNamespace(
        id=1, timestamp=_NOW, underlying="EURUSD", tenor=tenor, dte=30,
        sigma_mid=Decimal("7.50"), sigma_fair=Decimal("7.40"),
        ecart=Decimal("0.10"), signal_type=signal, rv=Decimal("7.60"),
    )


def _vol_row() -> SimpleNamespace:
    return SimpleNamespace(
        timestamp=_NOW, underlying="EURUSD",
        spot=Decimal("1.0857"), forward=Decimal("1.08600"),
    )


def _bt_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=1, strategy_name="vol_arb",
        start_date=date(2026, 1, 1), end_date=date(2026, 3, 31),
        sharpe_ratio=Decimal("1.5000"), max_drawdown_pct=Decimal("-8.0000"),
        total_return_pct=Decimal("12.0000"), n_trades=42,
        created_at=_NOW,
    )


@pytest.fixture
def fake_redis():
    r = AsyncMock()
    r.aclose = AsyncMock()
    r.get = AsyncMock(return_value=_NOW_ISO)   # heartbeat returns fresh timestamp
    return r


@pytest.fixture
def client(fake_redis):
    fake_session = AsyncMock()
    fake_session.execute = AsyncMock()

    async def _fake_db():
        yield fake_session

    with patch("api.main.aioredis.from_url", return_value=fake_redis):
        app = create_app()
        app.dependency_overrides[get_db_session] = _fake_db
        with TestClient(app) as c:
            c._fake_session = fake_session  # type: ignore[attr-defined]
            yield c


def _stub_scalars(client: TestClient, rows: list):
    """db.execute(...).scalars().all() returns ``rows``."""
    result = AsyncMock()
    result.scalars = lambda: SimpleNamespace(all=lambda: rows)
    client._fake_session.execute = AsyncMock(return_value=result)


def _stub_mixed(client: TestClient, responses: list):
    """Sequence of db.execute responses — for endpoints that call execute multiple times."""
    calls = iter(responses)
    client._fake_session.execute = AsyncMock(side_effect=lambda *a, **kw: next(calls))


@pytest.mark.unit
class TestSignalsRoute:
    def test_returns_rows(self, client):
        _stub_scalars(client, [_signal_row("1M"), _signal_row("3M", "FAIR")])
        r = client.get("/api/v1/signals?limit=10")
        body = r.json()
        assert r.status_code == 200
        assert len(body) == 2
        assert body[1]["signal_type"] == "FAIR"

    @pytest.mark.parametrize("bad_field,bad_value", [
        ("signal_type", "WEIRD"), ("limit", 0), ("limit", 3000),
    ])
    def test_validation_rejects_bad_params(self, client, bad_field, bad_value):
        r = client.get(f"/api/v1/signals?{bad_field}={bad_value}")
        assert r.status_code == 422

    def test_accepts_all_filters_combined(self, client):
        _stub_scalars(client, [_signal_row()])
        since = (_NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        r = client.get(
            "/api/v1/signals",
            params={"underlying": "EURUSD", "tenor": "1M",
                    "signal_type": "CHEAP", "since": since},
        )
        assert r.status_code == 200


@pytest.mark.unit
class TestVolHistoryRoute:
    def test_returns_light_rows(self, client):
        _stub_scalars(client, [_vol_row()])
        r = client.get("/api/v1/vol-history?symbol=EURUSD&limit=10")
        body = r.json()
        assert r.status_code == 200
        assert set(body[0]) == {"timestamp", "underlying", "spot", "forward"}


@pytest.mark.unit
class TestBacktestRoute:
    def test_returns_runs(self, client):
        _stub_scalars(client, [_bt_row()])
        r = client.get("/api/v1/backtest")
        body = r.json()
        assert r.status_code == 200
        assert body[0]["strategy_name"] == "vol_arb"
        assert body[0]["n_trades"] == 42


@pytest.mark.unit
class TestSystemStatsRoute:
    def test_aggregates_counts_and_heartbeats(self, client, fake_redis):
        # 4 calls of db.execute for counts : signals, vol_surfaces, position_snapshots, backtests
        count_results = []
        for n in (100, 50, 500, 3):
            res = AsyncMock()
            res.scalar_one = lambda nn=n: nn
            count_results.append(res)
        _stub_mixed(client, count_results)

        # Engine heartbeats all fresh (fixture default)
        r = client.get("/api/v1/system-stats")
        body = r.json()
        assert r.status_code == 200
        assert body["counts"] == {
            "signals": 100, "vol_surfaces": 50,
            "position_snapshots": 500, "backtest_runs": 3,
        }
        assert [e["name"] for e in body["engines"]] == [
            "market_data", "vol_engine", "risk_engine"
        ]
        assert all(e["state"] == "OK" for e in body["engines"])

    def test_engine_down_when_heartbeat_missing(self, client, fake_redis):
        # Counts = 0 for all 4 tables
        count_results = []
        for _ in range(4):
            res = AsyncMock()
            res.scalar_one = lambda: 0
            count_results.append(res)
        _stub_mixed(client, count_results)
        fake_redis.get = AsyncMock(return_value=None)

        body = client.get("/api/v1/system-stats").json()
        assert all(e["state"] == "DOWN" for e in body["engines"])
        assert all(e["heartbeat_age_s"] is None for e in body["engines"])
