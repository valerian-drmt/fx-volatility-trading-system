"""get_term_structure propagates the fair-vol fields from the surface (R11).

Mocks get_latest_surface (the Redis read) and asserts the per-tenor σ_fair^Q /
σ_fair^P / VRP / regime + the surface-level RV land on the response rows.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_asyncio")
pytestmark = pytest.mark.asyncio


def _surface() -> SimpleNamespace:
    return SimpleNamespace(
        symbol="EURUSD",
        timestamp=datetime(2026, 6, 17, 12, tzinfo=UTC),
        surface={
            "1M": {"atm": {"iv": 0.065}, "dte": 30},
            "3M": {"atm": {"iv": 0.072}, "dte": 90},
            "_rv_full_pct": 6.0,
            "_fair_q": {
                "1M": {"sigma_fair_p_pct": 5.2, "vrp_vol_pts": 0.6, "sigma_fair_q_pct": 5.8, "regime": "calm"},
                # 3M intentionally absent -> fair fields stay None for it
            },
        },
    )


async def test_term_structure_propagates_fair_and_rv(monkeypatch):
    from api.orchestration import vol_service

    async def _fake_latest(_redis, _symbol):
        return _surface()

    monkeypatch.setattr(vol_service, "get_latest_surface", _fake_latest)
    resp = await vol_service.get_term_structure(redis=None, symbol="EURUSD")
    by = {r.tenor: r for r in resp.pillars}

    assert by["1M"].sigma_atm_pct == pytest.approx(6.5)        # 0.065 * 100
    assert by["1M"].sigma_fair_q_pct == pytest.approx(5.8)
    assert by["1M"].sigma_fair_p_pct == pytest.approx(5.2)
    assert by["1M"].vrp_vol_pts == pytest.approx(0.6)
    assert by["1M"].sigma_fair_pct == pytest.approx(5.8)        # legacy = Q when present
    assert by["1M"].regime == "calm"
    assert by["1M"].rv_pct == pytest.approx(6.0)                # surface-level, all tenors

    # 3M has no _fair_q entry -> fair fields None, but RV still attached.
    assert by["3M"].sigma_fair_q_pct is None
    assert by["3M"].sigma_fair_pct is None
    assert by["3M"].rv_pct == pytest.approx(6.0)
