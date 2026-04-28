"""Unit tests for services.vol.chain_fetcher — tenor_label + concurrent gather.

The IB-side calls (reqContractDetailsAsync, reqSecDefOptParamsAsync,
reqMktData) are heavily mocked — integration is tested manually via
the live IB Gateway on sandbox/r9-pipeline-verif.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_asyncio")


def test_tenor_label_buckets() -> None:
    from services.vol.chain_fetcher import tenor_label

    assert tenor_label(30) == "1M"
    assert tenor_label(45) == "1M"
    assert tenor_label(46) == "2M"
    assert tenor_label(75) == "2M"
    assert tenor_label(90) == "3M"
    assert tenor_label(120) == "4M"
    assert tenor_label(150) == "5M"
    assert tenor_label(180) == "6M"
    assert tenor_label(300) == "6M"


@pytest.mark.asyncio
async def test_scan_all_tenors_semaphore_bounds_concurrency() -> None:
    """Semaphore(2) means max 2 scan_one_tenor coroutines in flight."""
    from services.vol import chain_fetcher

    in_flight = 0
    peak = 0

    async def fake_scan(_ib, chain, _F, **_kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return [(0.5, 0.08, 1.1)]  # one (delta, iv, strike)

    chain_fetcher.scan_one_tenor = fake_scan  # type: ignore[assignment]
    chains = [{"dte": d, "expiry": f"2026{d:03d}"} for d in (30, 60, 90, 120, 150)]

    out = await chain_fetcher.scan_all_tenors_concurrent(
        ib=MagicMock(), F=1.10, chains=chains, max_concurrent=2,
    )
    assert len(out) == 5  # all five tenors returned a pillar
    assert peak <= 2      # bounded by the semaphore


@pytest.mark.asyncio
async def test_scan_all_tenors_drops_empty_results() -> None:
    """If a tenor returns no triples it should NOT appear in the output."""
    from services.vol import chain_fetcher

    async def half_empty(_ib, chain, _F, **_kwargs):
        # Odd dtes return nothing.
        if chain["dte"] % 60 == 0:
            return [(0.5, 0.08, 1.1)]
        return []

    chain_fetcher.scan_one_tenor = half_empty  # type: ignore[assignment]
    chains = [{"dte": d, "expiry": f"2026{d:03d}"} for d in (30, 60, 90, 120)]

    out = await chain_fetcher.scan_all_tenors_concurrent(
        ib=MagicMock(), F=1.10, chains=chains, max_concurrent=3,
    )
    # Only 60d and 120d (divisible by 60) should survive.
    assert set(out.keys()) == {"2M", "4M"}


def test_safe_float_handles_nan_and_none() -> None:
    from services.vol.chain_fetcher import _safe

    assert _safe(None) is None
    assert _safe(float("nan")) is None
    assert _safe(1.23) == pytest.approx(1.23)
    assert _safe("1.5") == pytest.approx(1.5)
    assert _safe("garbage") is None


@pytest.mark.asyncio
async def test_engine_compute_surface_awaits_coroutine_fetch() -> None:
    """_compute_surface must await fetch_fop_chain if it's a coroutine."""
    from services.vol.engine import VolEngine

    async def async_fetch(F):
        return {"1M": [(0.25, 0.08, 1.18), (0.50, 0.07, 1.10), (0.75, 0.075, 1.05)]}

    engine = VolEngine(
        ib=MagicMock(),
        redis=AsyncMock(),
        symbol="EURUSD",
        ib_host="ib-gateway", ib_port=4002, client_id=2,
        fetch_fop_chain=async_fetch,
        fetch_ohlc=lambda: None,
    )
    surface = await engine._compute_surface(F=1.10)
    assert "1M" in surface
    assert "atm" in surface["1M"]


@pytest.mark.asyncio
async def test_engine_compute_surface_still_accepts_sync_fetch() -> None:
    """Back-compat : sync callable should still work."""
    from services.vol.engine import VolEngine

    def sync_fetch(F):
        return {"1M": [(0.25, 0.08, 1.18), (0.50, 0.07, 1.10), (0.75, 0.075, 1.05)]}

    engine = VolEngine(
        ib=MagicMock(),
        redis=AsyncMock(),
        symbol="EURUSD",
        ib_host="ib-gateway", ib_port=4002, client_id=2,
        fetch_fop_chain=sync_fetch,
        fetch_ohlc=lambda: None,
    )
    surface = await engine._compute_surface(F=1.10)
    assert "1M" in surface
