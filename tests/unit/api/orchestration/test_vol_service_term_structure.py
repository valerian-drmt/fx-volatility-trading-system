"""Unit tests for the /vol/term-structure enrichment.

RR/BF smile metrics are computed live from the surface wings; the fair-vol /
RV fields are read from the engine enrichment (`_fair_q` / `rv_pct`) and stay
null until the vol-engine publishes them.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from api.orchestration import vol_service
from api.schemas.vol import SurfaceResponse

pytestmark = pytest.mark.unit


def test_wing_iv_pct_flat_nested_missing():
    assert vol_service._wing_iv_pct({"iv_25dc_pct": 8.1}, "25dc") == 8.1
    assert vol_service._wing_iv_pct({"25dc": {"iv": 0.081}}, "25dc") == pytest.approx(8.1)
    assert vol_service._wing_iv_pct({}, "25dc") is None


def test_rr_bf_from_wings():
    rr, bf = vol_service._rr_bf({"iv_25dc_pct": 8.0, "iv_25dp_pct": 7.0}, "25dc", "25dp", 7.5)
    assert rr == 1.0
    assert bf == 0.0  # (8 + 7) / 2 − 7.5
    # A missing wing → both None (no fabricated metric).
    assert vol_service._rr_bf({"iv_25dc_pct": 8.0}, "25dc", "25dp", 7.5) == (None, None)


@pytest.mark.asyncio
async def test_get_term_structure_enriches_and_skips_meta(monkeypatch):
    surf = SurfaceResponse(
        symbol="EURUSD",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        surface={
            # meta key (starts with _) → never a pillar row; here it is the
            # fair-vol source the row reads from.
            "_fair_q": {"1M": {"sigma_fair_q_pct": 9.0, "sigma_fair_p_pct": 8.5, "regime": "calm"}},
            "1M": {"dte": 30, "sigma_atm_pct": 7.5, "iv_25dc_pct": 8.0, "iv_25dp_pct": 7.0},
        },
    )

    async def _fake(redis, symbol):
        return surf

    monkeypatch.setattr(vol_service, "get_latest_surface", _fake)
    resp = await vol_service.get_term_structure(redis=None, symbol="EURUSD")

    assert [r.tenor for r in resp.pillars] == ["1M"]  # _fair_q meta key skipped
    row = resp.pillars[0]
    assert row.sigma_atm_pct == 7.5
    assert row.rr_25d_pct == 1.0  # live from the wings
    assert row.bf_25d_pct == 0.0
    assert row.sigma_fair_q_pct == 9.0  # read from _fair_q
    assert row.sigma_fair_pct == 9.0  # Q preferred over P
    assert row.regime == "calm"
    assert row.rv_pct is None  # nothing published → null, not fabricated
    assert row.rr_10d_pct is None  # no 10Δ wings in this pillar
