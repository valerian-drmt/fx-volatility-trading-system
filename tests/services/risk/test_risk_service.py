"""Unit tests for ``services.risk.engine.RiskEngine``."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.risk.engine import RiskEngine


def _fake_redis(
    spot: dict | None = None, surface: dict | None = None
) -> MagicMock:
    r = MagicMock()

    async def _get(key: str):
        if key.startswith("latest_spot:"):
            return json.dumps(spot) if spot else None
        if key.startswith("latest_vol_surface:"):
            return (
                json.dumps({"symbol": "EURUSD", "surface": surface}) if surface else None
            )
        return None

    r.get = AsyncMock(side_effect=_get)
    r.set = AsyncMock()
    r.publish = AsyncMock(return_value=1)
    return r


def _fake_ib() -> MagicMock:
    ib = MagicMock()
    ib.isConnected = MagicMock(return_value=True)
    ib.disconnect = MagicMock()
    return ib


SURFACE_FIXTURE = {
    "1M": {
        "atm": {"iv": 0.075, "strike": 1.08},
        "25dc": {"iv": 0.078, "strike": 1.09},
        "25dp": {"iv": 0.079, "strike": 1.07},
    }
}


@pytest.mark.asyncio
async def test_risk_cycle_reads_spot_and_surface_and_publishes(monkeypatch):
    from bus import publisher

    published: list[dict] = []
    heartbeats: list[str] = []

    async def fake_publish(_r, greeks, pnl_curve=None, timestamp=None):
        published.append({"greeks": greeks, "pnl_curve": pnl_curve})

    async def fake_hb(_r, name):
        heartbeats.append(name)

    monkeypatch.setattr(publisher, "publish_risk_update", fake_publish)
    monkeypatch.setattr(publisher, "set_heartbeat", fake_hb)

    positions = [
        {
            "option_type": "C",
            "quantity": 10,
            "strike": 1.08,
            "T": 30 / 365,
            "tenor": "1M",
            "cost_per_unit": 0.001,
        }
    ]

    engine = RiskEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot={"mid": 1.08}, surface=SURFACE_FIXTURE),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=3,
        fetch_positions=lambda: positions,
    )

    ok = await engine.run_cycle()
    assert ok is True
    assert len(published) == 1
    greeks = published[0]["greeks"]
    # 10 ATM calls → delta ≈ 5 (call delta ≈ 0.5 × 10 contracts)
    assert 4.5 < greeks["delta"] < 5.5
    assert greeks["gamma"] > 0
    assert greeks["vega"] > 0
    assert "risk_engine" in heartbeats
    # PnL curve present (non-empty positions).
    assert published[0]["pnl_curve"] is not None
    assert len(published[0]["pnl_curve"]["spots"]) == 120


@pytest.mark.asyncio
async def test_risk_skips_cycle_when_spot_missing(monkeypatch):
    from bus import publisher

    async def boom(*_a, **_kw):
        raise AssertionError("publisher must not fire without spot")

    monkeypatch.setattr(publisher, "publish_risk_update", boom)
    monkeypatch.setattr(publisher, "set_heartbeat", boom)

    engine = RiskEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot=None, surface=SURFACE_FIXTURE),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=3,
        fetch_positions=lambda: [],
    )

    assert await engine.run_cycle() is False


@pytest.mark.asyncio
async def test_risk_skips_cycle_when_surface_missing(monkeypatch):
    from bus import publisher

    async def boom(*_a, **_kw):
        raise AssertionError("publisher must not fire without surface")

    monkeypatch.setattr(publisher, "publish_risk_update", boom)
    monkeypatch.setattr(publisher, "set_heartbeat", boom)

    engine = RiskEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot={"mid": 1.08}, surface=None),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=3,
        fetch_positions=lambda: [],
    )

    assert await engine.run_cycle() is False


@pytest.mark.asyncio
async def test_risk_publishes_zero_greeks_when_book_empty(monkeypatch):
    """No position → all greeks zero, pnl_curve skipped to save compute."""
    from bus import publisher

    published: list[dict] = []

    async def fake_publish(_r, greeks, pnl_curve=None, timestamp=None):
        published.append({"greeks": greeks, "pnl_curve": pnl_curve})

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(publisher, "publish_risk_update", fake_publish)
    monkeypatch.setattr(publisher, "set_heartbeat", noop)

    engine = RiskEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot={"mid": 1.08}, surface=SURFACE_FIXTURE),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=3,
        fetch_positions=lambda: [],
    )

    ok = await engine.run_cycle()
    assert ok is True
    greeks = published[0]["greeks"]
    assert greeks["delta"] == 0.0
    assert greeks["gamma"] == 0.0
    assert greeks["vega"] == 0.0
    assert published[0]["pnl_curve"] is None  # skipped for empty book


@pytest.mark.asyncio
async def test_risk_handles_fut_position_as_pure_delta(monkeypatch):
    """Futures contribute 1 per unit to delta, nothing to gamma/vega."""
    from bus import publisher

    published: list[dict] = []

    async def fake_publish(_r, greeks, pnl_curve=None, timestamp=None):
        published.append({"greeks": greeks})

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(publisher, "publish_risk_update", fake_publish)
    monkeypatch.setattr(publisher, "set_heartbeat", noop)

    positions = [{"instrument_type": "FUT", "quantity": 3, "cost_per_unit": 1.07}]

    engine = RiskEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot={"mid": 1.08}, surface=SURFACE_FIXTURE),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=3,
        fetch_positions=lambda: positions,
    )

    assert await engine.run_cycle() is True
    greeks = published[0]["greeks"]
    assert greeks["delta"] == 3.0
    assert greeks["gamma"] == 0.0
