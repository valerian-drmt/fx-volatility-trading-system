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
            "1M": {
                "atm": {"iv": 0.065}, "dte": 30, "rv_pct": 5.5,  # per-tenor RV
                "25dc": {"iv": 0.066}, "25dp": {"iv": 0.068},     # wings → RR/BF
                "10dc": {"iv": 0.067}, "10dp": {"iv": 0.072},
            },
            "3M": {"atm": {"iv": 0.072}, "dte": 90},              # no RV/wings → fallbacks
            "_rv_full_pct": 6.0,
            "_fair_q": {
                "1M": {"sigma_fair_p_pct": 5.2, "vrp_vol_pts": 0.6, "sigma_fair_q_pct": 5.8, "regime": "calm"},
                # 3M intentionally absent -> fair fields stay None for it
            },
        },
    )


async def test_term_structure_propagates_fair_and_rv(monkeypatch):
    from api.orchestration import vol_service

    async def _fake_latest(_redis, _symbol, db=None):
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
    assert by["1M"].rv_pct == pytest.approx(5.5)                # horizon-matched per-tenor RV
    # smile : RR = call − put, BF = ½(call+put) − ATM, in vol points.
    assert by["1M"].rr_25d_pct == pytest.approx((0.066 - 0.068) * 100)   # −0.2
    assert by["1M"].bf_25d_pct == pytest.approx(((0.066 + 0.068) / 2 - 0.065) * 100)  # +0.2
    assert by["1M"].rr_10d_pct == pytest.approx((0.067 - 0.072) * 100)   # −0.5
    assert by["1M"].bf_10d_pct == pytest.approx(((0.067 + 0.072) / 2 - 0.065) * 100)  # +0.45

    # 3M has no _fair_q entry -> fair fields None ; no per-tenor RV/wings → fallbacks/None.
    assert by["3M"].sigma_fair_q_pct is None
    assert by["3M"].sigma_fair_pct is None
    assert by["3M"].rv_pct == pytest.approx(6.0)
    assert by["3M"].rr_25d_pct is None
    assert by["3M"].bf_25d_pct is None


async def test_term_structure_market_closed_uses_fair_q_only(monkeypatch):
    """Markets closed: the persisted surface carries only the model sub-dicts
    (_fair_q, _rv_full_pct) with NO live IV pillars. The term structure must
    still serve the fair-vol / RV curve (σ_atm None) instead of pillars:[]."""
    from api.orchestration import vol_service

    closed = SimpleNamespace(
        symbol="EURUSD",
        timestamp=datetime(2026, 6, 17, 22, tzinfo=UTC),
        surface={
            "_rv_full_pct": 6.0,
            "_fair_q": {
                "1M": {"sigma_fair_q_pct": 5.8, "sigma_fair_p_pct": 5.2, "vrp_vol_pts": 0.6, "regime": "calm"},
                "3M": {"sigma_fair_q_pct": 6.1, "sigma_fair_p_pct": 5.5, "vrp_vol_pts": 0.5, "regime": "calm"},
            },
        },
    )

    async def _fake_latest(_redis, _symbol, db=None):
        return closed

    monkeypatch.setattr(vol_service, "get_latest_surface", _fake_latest)
    resp = await vol_service.get_term_structure(redis=None, symbol="EURUSD")
    by = {r.tenor: r for r in resp.pillars}

    assert set(by) == {"1M", "3M"}                     # only tenors with _fair_q data
    assert by["1M"].sigma_atm_pct is None              # no live IV grid
    assert by["1M"].sigma_fair_q_pct == pytest.approx(5.8)
    assert by["1M"].sigma_fair_pct == pytest.approx(5.8)   # legacy = Q
    assert by["1M"].rv_pct == pytest.approx(6.0)       # surface-level RV fallback
    assert by["1M"].rr_25d_pct is None                 # no wings market-closed
    assert by["3M"].sigma_fair_q_pct == pytest.approx(6.1)


class _Res:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _FakeDB:
    """Serves canned scalar_one_or_none rows in call order (grid query first)."""

    def __init__(self, rows):
        self._rows = list(rows)

    async def execute(self, _stmt):
        return _Res(self._rows.pop(0))


class _NoRedis:
    async def get(self, _key):
        return None  # force the DB fallback


async def test_get_latest_surface_prefers_full_grid_over_model_only():
    """Markets closed: the latest rows are model-only / sparse; the DB fallback's
    first (jsonb_exists_all) query returns the last COMPLETE grid (all display
    pillars) — that row must win over the newer model-only one."""
    from types import SimpleNamespace

    from api.orchestration import vol_service

    grid = SimpleNamespace(
        underlying="EURUSD",
        timestamp=datetime(2026, 7, 31, 20, 59, tzinfo=UTC),
        surface_data={t: {"atm": {"iv": 0.06}} for t in ("1M", "2M", "3M", "4M", "5M", "6M")}
        | {"_fair_q": {}},
    )
    resp = await vol_service.get_latest_surface(_NoRedis(), "EURUSD", db=_FakeDB([grid]))
    assert resp.timestamp == grid.timestamp
    assert {"1M", "2M", "3M", "4M", "5M", "6M"} <= set(resp.surface)   # all pillars survived


async def test_get_latest_surface_falls_back_to_latest_when_no_grid():
    """No full grid anywhere (fresh DB / only model-only rows): the grid query
    returns None, then the plain-latest query serves the model-only surface so the
    fair-vol term structure still has something to work from."""
    from types import SimpleNamespace

    from api.orchestration import vol_service

    latest = SimpleNamespace(
        underlying="EURUSD",
        timestamp=datetime(2026, 7, 31, 21, 59, tzinfo=UTC),
        surface_data={"_fair_q": {"1M": {"sigma_fair_q_pct": 5.8}}},
    )
    # grid query → None, then fallback query → the model-only latest row.
    resp = await vol_service.get_latest_surface(_NoRedis(), "EURUSD", db=_FakeDB([None, latest]))
    assert resp.timestamp == latest.timestamp
