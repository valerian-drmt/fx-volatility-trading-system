"""Unit tests for the market-data historical-bars fetcher.

The pure epoch conversion is tested directly; the IB round-trip is tested with
a fake IB (skipped when ib_insync isn't installed, since the module imports
``Forex`` lazily).
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from engines.market_data.bars_fetcher import TF_SPECS, _to_epoch_ms, fetch_bars


def test_to_epoch_ms_from_naive_datetime_is_utc():
    dt = datetime(2024, 1, 1, 0, 0, 0)
    assert _to_epoch_ms(dt) == 1_704_067_200_000


def test_to_epoch_ms_from_bare_date():
    # Regression: IB returns DAILY bars (the 1Y timeframe) with a bare
    # datetime.date, not epoch seconds. float(date) used to raise TypeError,
    # which — being outside the per-tf try — left every chart empty in prod.
    assert _to_epoch_ms(date(2024, 1, 1)) == 1_704_067_200_000


def test_to_epoch_ms_from_aware_datetime():
    dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert _to_epoch_ms(dt) == 1_704_067_200_000


def test_to_epoch_ms_from_epoch_seconds():
    assert _to_epoch_ms(1_704_067_200) == 1_704_067_200_000
    assert _to_epoch_ms("1704067200") == 1_704_067_200_000


class _FakeIB:
    """Returns two out-of-order bars for every timeframe; records the calls."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def qualifyContractsAsync(self, contract):
        return [contract]

    async def reqHistoricalDataAsync(self, contract, **kw):
        self.calls.append(kw)
        return [
            SimpleNamespace(date=1_704_070_800, open=1.10, high=1.11, low=1.09, close=1.105),
            SimpleNamespace(date=1_704_067_200, open=1.09, high=1.10, low=1.08, close=1.10),
        ]


@pytest.mark.asyncio
async def test_fetch_bars_normalises_and_sorts():
    pytest.importorskip("ib_insync")
    ib = _FakeIB()
    out = await fetch_bars(ib, "EURUSD")

    assert set(out) == set(TF_SPECS)
    for tf in TF_SPECS:
        rows = out[tf]
        assert len(rows) == 2
        # ascending by t, epoch → ms, OHLC floats
        assert rows[0]["t"] < rows[1]["t"]
        assert rows[0] == {"t": 1_704_067_200_000, "o": 1.09, "h": 1.10, "l": 1.08, "c": 1.10}
    # MIDPOINT + 24h session requested
    assert ib.calls[0]["whatToShow"] == "MIDPOINT"
    assert ib.calls[0]["useRTH"] is False


@pytest.mark.asyncio
async def test_fetch_bars_qualify_failure_returns_empty():
    pytest.importorskip("ib_insync")

    class _BadIB(_FakeIB):
        async def qualifyContractsAsync(self, contract):
            raise RuntimeError("no session")

    out = await fetch_bars(_BadIB(), "EURUSD")
    assert out == {tf: [] for tf in TF_SPECS}


@pytest.mark.asyncio
async def test_fetch_bars_one_bad_timeframe_does_not_break_others():
    """A single timeframe whose rows fail to convert must map to [] on its own,
    never propagate and empty the whole result (the prod bars regression)."""
    pytest.importorskip("ib_insync")

    class _MixedIB(_FakeIB):
        async def reqHistoricalDataAsync(self, contract, **kw):
            self.calls.append(kw)
            if kw["durationStr"] == "1 Y":
                # An unconvertible date — as the daily-date TypeError was before the fix.
                return [SimpleNamespace(date=object(), open=1.1, high=1.1, low=1.1, close=1.1)]
            return [SimpleNamespace(date=1_704_067_200, open=1.09, high=1.10, low=1.08, close=1.10)]

    out = await fetch_bars(_MixedIB(), "EURUSD")
    assert out["1Y"] == []                       # the bad one is isolated
    assert len(out["1D"]) == 1                    # the others still populate
    assert len(out["1W"]) == 1
    assert len(out["1M"]) == 1
