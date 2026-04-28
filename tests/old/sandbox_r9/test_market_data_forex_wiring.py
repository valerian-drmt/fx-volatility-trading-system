"""Tests for the sandbox/r9 fix : real Forex ticker subscription in market-data.

Covers :
- ``MARKET_SYMBOL`` default from ``shared.config.Settings``
- ``MarketDataEngine`` exposes a ``post_connect_hook`` param + calls it
  once after IB is connected
- ``_subscribe_ib_ticks`` calls ``ib.qualifyContractsAsync`` then
  ``ib.reqMktData`` and wires ``ticker.updateEvent`` to the callback
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_asyncio")


def test_market_symbol_default_is_eurusd() -> None:
    from shared.config import Settings

    s = Settings()
    assert s.MARKET_SYMBOL == "EURUSD"


@pytest.mark.asyncio
async def test_engine_calls_post_connect_hook_once() -> None:
    from services.market_data.engine import MarketDataEngine

    ib = MagicMock()
    ib.isConnected = MagicMock(side_effect=[False, True])
    ib.disconnect = MagicMock()
    redis = AsyncMock()
    calls: list[str] = []

    async def hook() -> None:
        calls.append("called")

    engine = MarketDataEngine(
        ib=ib,
        redis=redis,
        symbol="EURUSD",
        ib_host="ib-gateway",
        ib_port=4002,
        client_id=1,
        fetch_latest_tick=lambda: None,
        post_connect_hook=hook,
    )

    # Stub the connect backoff to avoid touching real IB.
    import shared.ib_connection as ibc

    async def _noop(*_a: Any, **_kw: Any) -> None:
        return None

    ibc.connect_ib_with_backoff = _noop  # type: ignore[assignment]

    # Ask the engine to exit after the first cycle.
    engine._stop.set()
    await engine.run()

    assert calls == ["called"]


@pytest.mark.asyncio
async def test_subscribe_ib_ticks_qualifies_and_subscribes() -> None:
    # Late import so the test collection does not require ib_insync.
    from services.market_data import main as md_main

    ib = MagicMock()
    ib.qualifyContractsAsync = AsyncMock()
    ticker = MagicMock()
    ticker.updateEvent = MagicMock()
    ticker.updateEvent.__iadd__ = MagicMock(return_value=ticker.updateEvent)
    ib.reqMktData = MagicMock(return_value=ticker)

    received: list[Any] = []

    def on_update(t: Any) -> None:
        received.append(t)

    # ib_insync.Forex may not be importable in the unit test env — patch it.
    import importlib
    import sys
    fake_mod = importlib.util.module_from_spec(
        importlib.machinery.ModuleSpec("ib_insync", loader=None)
    )

    class _Forex:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

    fake_mod.Forex = _Forex  # type: ignore[attr-defined]
    sys.modules["ib_insync"] = fake_mod

    await md_main._subscribe_ib_ticks(ib, on_update, "EURUSD")

    ib.qualifyContractsAsync.assert_awaited_once()
    ib.reqMktData.assert_called_once()
    # The callback was registered on the ticker update event.
    ticker.updateEvent.__iadd__.assert_called_once_with(on_update)
