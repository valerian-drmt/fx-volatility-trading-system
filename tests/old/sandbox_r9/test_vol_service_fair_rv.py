"""Tests for fair_vol + RV propagation through /term-structure and /smile.

The engine surface carries aggregate keys ``_garch`` (per-tenor fair vol)
and ``_rv_full_pct`` (realised vol). The API must copy these into the
term-structure pillars and onto the smile response so the frontend can
overlay reference lines.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pytest_asyncio")


def _redis_surface(payload: dict) -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(payload))
    return redis


def _surface_payload(atm_by_tenor, fair_by_tenor, rv=2.87):
    surface: dict = {
        tenor: {"atm": {"iv": iv, "strike": 1.17}}
        for tenor, iv in atm_by_tenor.items()
    }
    surface["_garch"] = {t: {"sigma_model_pct": f} for t, f in fair_by_tenor.items()}
    surface["_rv_full_pct"] = rv
    return {
        "symbol": "EURUSD",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "surface": surface,
    }


@pytest.mark.asyncio
async def test_term_structure_propagates_fair_and_rv_per_tenor() -> None:
    from api.services.vol_service import get_term_structure

    redis = _redis_surface(
        _surface_payload(
            {"1M": 0.06, "3M": 0.072, "6M": 0.085},
            {"1M": 2.3, "3M": 2.5, "6M": 2.7},
            rv=2.87,
        )
    )
    resp = await get_term_structure(redis, "EURUSD")
    by_tenor = {r.tenor: r for r in resp.pillars}
    assert by_tenor["1M"].sigma_atm_pct == pytest.approx(6.0)
    assert by_tenor["1M"].sigma_fair_pct == pytest.approx(2.3)
    assert by_tenor["1M"].rv_pct == pytest.approx(2.87)
    assert by_tenor["3M"].sigma_fair_pct == pytest.approx(2.5)
    assert by_tenor["6M"].sigma_fair_pct == pytest.approx(2.7)


@pytest.mark.asyncio
async def test_term_structure_returns_none_when_no_garch_for_tenor() -> None:
    from api.services.vol_service import get_term_structure

    # Only 1M has a GARCH fair ; 3M is missing from _garch.
    redis = _redis_surface(
        _surface_payload(
            {"1M": 0.06, "3M": 0.072},
            {"1M": 2.3},
            rv=2.87,
        )
    )
    resp = await get_term_structure(redis, "EURUSD")
    by_tenor = {r.tenor: r for r in resp.pillars}
    assert by_tenor["1M"].sigma_fair_pct == pytest.approx(2.3)
    assert by_tenor["3M"].sigma_fair_pct is None
    # RV is a surface-level aggregate — same value for every row.
    assert by_tenor["1M"].rv_pct == pytest.approx(2.87)
    assert by_tenor["3M"].rv_pct == pytest.approx(2.87)


@pytest.mark.asyncio
async def test_term_structure_handles_surface_without_garch_or_rv() -> None:
    from api.services.vol_service import get_term_structure

    # Strip both aggregate keys — legacy surface shape.
    payload = _surface_payload({"1M": 0.06}, {"1M": 2.3})
    payload["surface"].pop("_garch")
    payload["surface"].pop("_rv_full_pct")
    redis = _redis_surface(payload)
    resp = await get_term_structure(redis, "EURUSD")
    assert resp.pillars[0].sigma_fair_pct is None
    assert resp.pillars[0].rv_pct is None
