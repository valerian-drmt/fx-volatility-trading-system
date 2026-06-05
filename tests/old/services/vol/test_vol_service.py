"""Unit tests for ``engines.vol.engine.VolEngine``.

All IB/Redis calls are mocked. ``run_cycle()`` is exercised directly so
we don't have to fight the 180-second sleep.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest

from engines.vol.engine import VolEngine


def _fake_redis(spot_payload: dict | None = None) -> MagicMock:
    r = MagicMock()

    async def _get(_key: str):
        return json.dumps(spot_payload) if spot_payload else None

    r.get = AsyncMock(side_effect=_get)
    r.set = AsyncMock()
    r.publish = AsyncMock(return_value=1)
    return r


def _fake_ib() -> MagicMock:
    ib = MagicMock()
    ib.isConnected = MagicMock(return_value=True)
    ib.disconnect = MagicMock()
    return ib


def _fop_fixture(_F: float) -> dict[str, list[tuple[float, float, float]]]:
    """Symmetric smile on two tenors."""
    pillars = [
        (0.10, 0.085, 1.12),
        (0.25, 0.078, 1.10),
        (0.50, 0.072, 1.08),
        (0.75, 0.079, 1.06),
        (0.90, 0.086, 1.04),
    ]
    return {"1M": pillars, "3M": pillars}


def _ohlc_fixture(n: int = 60) -> pd.DataFrame:
    rng = np.random.default_rng(seed=11)
    returns = rng.normal(0, 0.008, size=n)
    closes = 1.08 * np.cumprod(1 + returns)
    opens = closes * (1 + rng.normal(0, 0.002, size=n))
    highs = np.maximum(opens, closes) * 1.001
    lows = np.minimum(opens, closes) * 0.999
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes})


@pytest.mark.asyncio
async def test_vol_cycle_end_to_end_publishes_surface(monkeypatch):
    """Happy path : spot present + FOP + OHLC → surface published + heartbeat."""
    from bus import publisher

    published: list[dict] = []
    heartbeats: list[str] = []

    async def fake_publish_vol(_r, symbol, surface_data, signals_data, timestamp=None):
        published.append({"symbol": symbol, "surface": surface_data})

    async def fake_set_heartbeat(_r, name):
        heartbeats.append(name)

    monkeypatch.setattr(publisher, "publish_vol_update", fake_publish_vol)
    monkeypatch.setattr(publisher, "set_heartbeat", fake_set_heartbeat)

    engine = VolEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot_payload={"mid": 1.08, "bid": 1.0799, "ask": 1.0801}),
        symbol="EURUSD",
        ib_host="ib-gateway",
        ib_port=4002,
        client_id=2,
        fetch_fop_chain=_fop_fixture,
        fetch_ohlc=_ohlc_fixture,
    )

    ok = await engine.run_cycle()

    assert ok is True
    assert len(published) == 1
    assert published[0]["symbol"] == "EURUSD"
    # PCHIP passes through the ATM observation exactly.
    assert published[0]["surface"]["1M"]["atm"]["iv"] == pytest.approx(0.072, abs=1e-9)
    assert "vol_engine" in heartbeats


@pytest.mark.asyncio
async def test_vol_cycle_skips_when_spot_missing(monkeypatch):
    """No spot in Redis → no FOP fetch, no publish, no heartbeat."""
    from bus import publisher

    async def boom(*_a, **_kw):
        raise AssertionError("publisher must not be called when spot is missing")

    monkeypatch.setattr(publisher, "publish_vol_update", boom)
    monkeypatch.setattr(publisher, "set_heartbeat", boom)

    fop_calls: list[float] = []

    def fop_tracer(F: float):
        fop_calls.append(F)
        return {}

    engine = VolEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot_payload=None),  # GET returns None
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=2,
        fetch_fop_chain=fop_tracer,
        fetch_ohlc=lambda: None,
    )

    ok = await engine.run_cycle()
    assert ok is False
    assert fop_calls == []  # short-circuit before fetching FOP


@pytest.mark.asyncio
async def test_vol_cycle_publishes_even_when_ohlc_empty(monkeypatch):
    """RV/GARCH degrade gracefully : empty OHLC still publishes the smile."""
    from bus import publisher

    published: list[dict] = []

    async def fake_publish_vol(_r, symbol, surface_data, signals_data, timestamp=None):
        published.append({"surface": surface_data})

    async def noop(*_a, **_kw):
        return None

    monkeypatch.setattr(publisher, "publish_vol_update", fake_publish_vol)
    monkeypatch.setattr(publisher, "set_heartbeat", noop)

    engine = VolEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot_payload={"mid": 1.08}),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=2,
        fetch_fop_chain=_fop_fixture,
        fetch_ohlc=lambda: None,  # no OHLC
    )

    ok = await engine.run_cycle()
    assert ok is True
    assert "_rv_full_pct" not in published[0]["surface"]
    assert "_garch" not in published[0]["surface"]


@pytest.mark.asyncio
async def test_vol_read_spot_handles_malformed_payload():
    engine = VolEngine(
        ib=_fake_ib(),
        redis=_fake_redis(spot_payload={"not_a_mid": "oops"}),
        symbol="EURUSD",
        ib_host="h",
        ib_port=4002,
        client_id=2,
        fetch_fop_chain=lambda _F: {},
        fetch_ohlc=lambda: None,
    )
    assert await engine._read_spot() is None
