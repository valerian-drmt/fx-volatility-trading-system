"""Tests for api.services.vol_service.get_term_structure — schema tolerance.

The vol-engine publishes surfaces in the shape ``{tenor: {atm: {iv, strike}}}``
while the API used to expect ``{tenor: {sigma_atm_pct, dte}}``. The fix
supports both plus skips engine-level aggregates prefixed with ``_``.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pytest_asyncio")


def _redis_returning(payload: dict) -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=json.dumps(payload))
    return redis


@pytest.mark.asyncio
async def test_engine_shape_extracts_atm_iv_in_percent() -> None:
    from api.services.vol_service import get_term_structure

    surface = {
        "symbol": "EURUSD",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "surface": {
            "1W": {"atm": {"iv": 0.065, "strike": 1.17}, "25dc": {"iv": 0.068, "strike": 1.18}},
            "1M": {"atm": {"iv": 0.072, "strike": 1.17}},
            "_rv_full_pct": 2.87,
            "_garch": {"1W": {"sigma_model_pct": 2.386}},
        },
    }
    redis = _redis_returning(surface)
    resp = await get_term_structure(redis, "EURUSD")

    atm_by_tenor = {row.tenor: row.sigma_atm_pct for row in resp.pillars}
    assert atm_by_tenor == {
        "1W": pytest.approx(6.5),
        "1M": pytest.approx(7.2),
    }
    # Engine aggregates prefixed with _ must be skipped.
    assert "_rv_full_pct" not in atm_by_tenor
    assert "_garch" not in atm_by_tenor


@pytest.mark.asyncio
async def test_legacy_sigma_atm_pct_shape_still_works() -> None:
    from api.services.vol_service import get_term_structure

    surface = {
        "symbol": "EURUSD",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "surface": {
            "1W": {"sigma_atm_pct": 6.5, "dte": 7},
            "1M": {"sigma_atm_pct": 7.2, "dte": 30},
        },
    }
    redis = _redis_returning(surface)
    resp = await get_term_structure(redis, "EURUSD")

    rows = {row.tenor: (row.sigma_atm_pct, row.dte) for row in resp.pillars}
    assert rows == {"1W": (pytest.approx(6.5), 7), "1M": (pytest.approx(7.2), 30)}


@pytest.mark.asyncio
async def test_missing_atm_returns_none_sigma() -> None:
    from api.services.vol_service import get_term_structure

    surface = {
        "symbol": "EURUSD",
        "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "surface": {
            "1W": {"25dc": {"iv": 0.068, "strike": 1.18}},  # no atm key
        },
    }
    redis = _redis_returning(surface)
    resp = await get_term_structure(redis, "EURUSD")
    assert resp.pillars[0].sigma_atm_pct is None
