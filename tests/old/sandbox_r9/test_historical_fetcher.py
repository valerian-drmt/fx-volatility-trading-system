"""Tests for services.vol.historical_fetcher.fetch_daily_ohlc.

IB API is mocked — we only assert the contract definition, the DataFrame
shape, the cache behaviour and the graceful fallback on empty/errored
responses.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_asyncio")


@dataclass
class FakeBar:
    date: str
    open: float
    high: float
    low: float
    close: float


FAKE_BARS = [
    FakeBar("2026-04-18", 1.165, 1.170, 1.162, 1.168),
    FakeBar("2026-04-19", 1.168, 1.175, 1.167, 1.173),
    FakeBar("2026-04-20", 1.173, 1.178, 1.171, 1.176),
    FakeBar("2026-04-21", 1.176, 1.180, 1.173, 1.179),
    FakeBar("2026-04-22", 1.179, 1.185, 1.176, 1.177),
]


@pytest.fixture(autouse=True)
def _reset_cache():
    from services.vol.historical_fetcher import reset_cache

    reset_cache()
    yield
    reset_cache()


@pytest.mark.asyncio
async def test_returns_dataframe_sorted_by_date() -> None:
    from services.vol.historical_fetcher import fetch_daily_ohlc

    ib = MagicMock()
    # Emit the bars in reverse order — fetcher must sort ascending.
    ib.reqHistoricalDataAsync = AsyncMock(return_value=list(reversed(FAKE_BARS)))

    df = await fetch_daily_ohlc(ib, duration_str="5 D")
    assert df is not None
    assert list(df.columns) == ["date", "open", "high", "low", "close"]
    assert list(df["date"]) == [b.date for b in FAKE_BARS]


@pytest.mark.asyncio
async def test_requests_contfut_contract_with_expected_params() -> None:
    from services.vol.historical_fetcher import fetch_daily_ohlc

    ib = MagicMock()
    ib.reqHistoricalDataAsync = AsyncMock(return_value=FAKE_BARS)

    await fetch_daily_ohlc(ib, duration_str="1 Y")
    call = ib.reqHistoricalDataAsync.await_args
    contract = call.args[0]
    assert contract.symbol == "EUR"
    assert contract.secType == "CONTFUT"
    assert contract.exchange == "CME"
    assert contract.currency == "USD"
    assert call.kwargs["durationStr"] == "1 Y"
    assert call.kwargs["barSizeSetting"] == "1 day"
    assert call.kwargs["whatToShow"] == "ADJUSTED_LAST"
    assert call.kwargs["useRTH"] is True


@pytest.mark.asyncio
async def test_second_call_within_ttl_hits_cache_and_skips_ib() -> None:
    from services.vol.historical_fetcher import fetch_daily_ohlc

    ib = MagicMock()
    ib.reqHistoricalDataAsync = AsyncMock(return_value=FAKE_BARS)

    df1 = await fetch_daily_ohlc(ib)
    df2 = await fetch_daily_ohlc(ib)
    assert df1 is df2
    assert ib.reqHistoricalDataAsync.await_count == 1


@pytest.mark.asyncio
async def test_returns_none_when_ib_returns_empty_list() -> None:
    from services.vol.historical_fetcher import fetch_daily_ohlc

    ib = MagicMock()
    ib.reqHistoricalDataAsync = AsyncMock(return_value=[])

    assert await fetch_daily_ohlc(ib) is None


@pytest.mark.asyncio
async def test_returns_none_on_reqHistoricalData_exception() -> None:
    from services.vol.historical_fetcher import fetch_daily_ohlc

    ib = MagicMock()
    ib.reqHistoricalDataAsync = AsyncMock(side_effect=RuntimeError("no data farm"))

    assert await fetch_daily_ohlc(ib) is None
