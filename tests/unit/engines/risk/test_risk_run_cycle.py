"""Risk engine cycle (#339, audit 07#3): the cycle publishes nothing when it
has no spot (no fabricated greeks), falls back to the IB portfolio mark for
spot, counts the legs priced on the fallback vol in the published payload,
tolerates a garbage surface, and reports a publish failure instead of
raising out of the loop."""
from __future__ import annotations

import json
from typing import Any

import pytest

from bus import keys
from engines.risk.engine import FALLBACK_IV, RiskEngine

pytestmark = pytest.mark.asyncio


class _Redis:
    def __init__(self, kv: dict[str, Any] | None = None, *, fail_publish: bool = False) -> None:
        self.kv = dict(kv or {})
        self.published: list[tuple[str, dict]] = []
        self.fail_publish = fail_publish

    async def get(self, name: str) -> Any:
        return self.kv.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> None:
        self.kv[name] = value

    async def publish(self, channel: str, message: str) -> int:
        if self.fail_publish:
            raise ConnectionError("redis down")
        self.published.append((channel, json.loads(message)))
        return 1

    async def hgetall(self, name: str) -> dict:
        return {}


class _Contract:
    def __init__(self, symbol: str = "EUR", sec_type: str = "FUT") -> None:
        self.symbol = symbol
        self.secType = sec_type


class _PortfolioItem:
    def __init__(self, mark: float, symbol: str = "EUR", sec_type: str = "FUT") -> None:
        self.contract = _Contract(symbol, sec_type)
        self.marketPrice = mark


class _IB:
    def __init__(self, portfolio: list | None = None) -> None:
        self._portfolio = portfolio or []

    def isConnected(self) -> bool:
        return True

    def portfolio(self) -> list:
        return self._portfolio


def _option(**over: Any) -> dict:
    base = {"id": 1, "instrument_type": "OPTION", "option_type": "C", "quantity": 2,
            "strike": 1.10, "T": 0.25, "tenor": "3M", "symbol": "EUR"}
    base.update(over)
    return base


def _engine(redis: _Redis, ib: _IB | None = None, positions: list[dict] | None = None) -> RiskEngine:
    return RiskEngine(
        ib=ib or _IB(), redis=redis, symbol="EURUSD", ib_host="h", ib_port=1, client_id=3,
        fetch_positions=lambda: list(positions or []),
    )


def _greeks_published(r: _Redis) -> dict:
    return json.loads(r.kv[keys.LATEST_GREEKS_PORTFOLIO])["greeks"]


async def test_no_spot_anywhere_publishes_nothing() -> None:
    r = _Redis()
    eng = _engine(r, positions=[_option()])
    assert await eng.run_cycle() is False
    assert r.published == [] and keys.LATEST_GREEKS_PORTFOLIO not in r.kv


async def test_spot_falls_back_to_the_portfolio_future_mark() -> None:
    r = _Redis()
    eng = _engine(r, ib=_IB([_PortfolioItem(0.0123, sec_type="OPT"), _PortfolioItem(1.1150)]), positions=[_option()])
    assert await eng.run_cycle() is True
    assert _greeks_published(r)["spot"] == pytest.approx(1.1150)


async def test_spot_envelope_is_read_and_fallback_legs_are_counted_when_the_surface_is_missing() -> None:
    r = _Redis({keys.LATEST_SPOT.format(symbol="EURUSD"): json.dumps({"value": 1.1, "ts": "2026-11-07T10:00:00Z"})})
    eng = _engine(r, positions=[_option(), _option(id=2, option_type="P", strike=1.08)])
    assert await eng.run_cycle() is True
    g = _greeks_published(r)
    assert g["iv_fallback_legs"] == 2, "both legs priced on FALLBACK_IV with no surface"
    assert g["n_positions"] == 2 and g["n_priced"] == 2
    assert g["spot"] == pytest.approx(1.1) and g["vega"] > 0
    assert await eng.run_cycle() is True
    assert _greeks_published(r)["iv_fallback_legs"] == 2, "the counter does not accumulate across cycles"
    channels = [c for c, _ in r.published]
    assert "risk_update" in channels
    assert r.kv[keys.HEARTBEAT.format(engine_name=keys.ENGINE_RISK)]


async def test_a_surface_pillar_is_preferred_over_the_fallback_vol() -> None:
    surface = {"surface": {"3M": {
        "25dp": {"iv": 0.071, "strike": 1.07}, "atm": {"iv": 0.065, "strike": 1.10}, "25dc": {"iv": 0.068, "strike": 1.13},
    }}}
    r = _Redis({
        keys.LATEST_SPOT.format(symbol="EURUSD"): "1.1",
        keys.LATEST_VOL_SURFACE.format(symbol="EURUSD"): json.dumps(surface),
    })
    eng = _engine(r, positions=[_option()])
    assert await eng.run_cycle() is True
    assert _greeks_published(r)["iv_fallback_legs"] == 0


async def test_garbage_surface_reads_as_none_and_does_not_break_the_cycle() -> None:
    r = _Redis({
        keys.LATEST_SPOT.format(symbol="EURUSD"): "1.1",
        keys.LATEST_VOL_SURFACE.format(symbol="EURUSD"): "{not json",
    })
    eng = _engine(r, positions=[_option()])
    assert await eng._read_surface() is None
    assert await eng.run_cycle() is True
    assert _greeks_published(r)["iv_fallback_legs"] == 1


async def test_publish_failure_is_reported_not_raised() -> None:
    r = _Redis({keys.LATEST_SPOT.format(symbol="EURUSD"): "1.1"}, fail_publish=True)
    eng = _engine(r, positions=[_option()])
    assert await eng.run_cycle() is False


def test_fallback_vol_is_the_documented_constant() -> None:
    assert abs(FALLBACK_IV - 0.08) < 1e-12
