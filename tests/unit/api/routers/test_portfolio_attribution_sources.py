"""#377 — the three per-leg panels must foot: shared tenor bucket, one source
filter for totals and groups, vega in vol points, a spot fallback for dS.

Live evidence (prod, 2026-11-06): every IB option leg had delta/gamma
attribution None because the only 6E future was opened inside the window;
legs tagged tenor "1W" sat under "1M" in the matrix and "1W" in the breakdown;
the by-tenor total summed booked rows the by-trade panel filtered out; and the
vega term used dσ as a fraction against a per-vol-point vega.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from api.routers import portfolio_panel as pp
from core.vol.tenors import TENOR_LADDER, tenor_bucket_from_expiry

TODAY = date(2026, 11, 6)


@pytest.mark.parametrize(
    "dte, bucket",
    [(0, "1M"), (1, "1M"), (45, "1M"), (46, "2M"), (75, "2M"), (90, "3M"), (105, "3M"),
     (106, "4M"), (129, "4M"), (150, "5M"), (165, "5M"), (166, "6M"), (400, "6M")],
)
def test_tenor_bucket_from_expiry_clamps_onto_the_ladder(dte: int, bucket: str) -> None:
    assert tenor_bucket_from_expiry(TODAY + timedelta(days=dte), TODAY) == bucket
    assert bucket in TENOR_LADDER


def test_tenor_bucket_none_without_expiry_and_router_alias_is_the_same_function() -> None:
    assert tenor_bucket_from_expiry(None, TODAY) is None
    # the attribution's bucket IS the core helper (one implementation)
    assert pp._attrib_tenor_bucket(TODAY + timedelta(days=129), TODAY) == "4M"
    assert pp._attrib_tenor_bucket(TODAY + timedelta(days=129), TODAY) == tenor_bucket_from_expiry(
        TODAY + timedelta(days=129), TODAY,
    )


def _rows() -> list[dict[str, Any]]:
    return [
        {"source": "ib_live", "attrib_tenor": "3M", "actual_pnl_usd": 100.0, "delta_pnl_usd": 60.0,
         "gamma_pnl_usd": 10.0, "vega_pnl_usd": 20.0, "theta_pnl_usd": 5.0, "residual_usd": 5.0},
        {"source": "ib_live", "attrib_tenor": "1M", "actual_pnl_usd": -40.0, "delta_pnl_usd": -30.0,
         "gamma_pnl_usd": 0.0, "vega_pnl_usd": -5.0, "theta_pnl_usd": -2.0, "residual_usd": -3.0},
        {"source": "booked", "attrib_tenor": "3M", "actual_pnl_usd": 999.0, "delta_pnl_usd": 999.0,
         "gamma_pnl_usd": 0.0, "vega_pnl_usd": 0.0, "theta_pnl_usd": 0.0, "residual_usd": 0.0},
    ]


def test_source_filter_makes_by_tenor_and_by_trade_totals_foot() -> None:
    ib = pp._filter_source(_rows(), "ib_live")
    assert [r["source"] for r in ib] == ["ib_live", "ib_live"]
    groups, totals = pp._attribution_groups(ib, "tenor")
    # sum of the per-leg rows == sum of the tenor groups == totals, on the SAME set
    assert totals["actual_pnl_usd"] == pytest.approx(60.0)
    assert sum(g["actual_pnl_usd"] for g in groups) == pytest.approx(60.0)
    assert sum(r["actual_pnl_usd"] for r in ib) == pytest.approx(60.0)
    # 'all' brings the booked row back, 'booked' isolates it
    assert len(pp._filter_source(_rows(), "all")) == 3
    assert [r["source"] for r in pp._filter_source(_rows(), "booked")] == ["booked"]


def test_ib_iv_fraction_becomes_vol_points_for_the_vega_term() -> None:
    assert pp._iv_fraction_to_pts(0.06051) == pytest.approx(6.051)
    assert pp._iv_fraction_to_pts(None) is None
    # end to end: vega 2779 $/pt, IV 6.05 -> 6.15 (+0.10 pt) must give ~$278, not ~$2.8
    out = pp._decompose_row(
        now=datetime(2026, 11, 6, 12, tzinfo=UTC), pnl_now=300.0, pnl_then=0.0,
        spot_now=1.12, spot_then=1.12,
        iv_now=pp._iv_fraction_to_pts(0.0615), iv_then=pp._iv_fraction_to_pts(0.0605),
        delta=0.0, gamma=0.0, vega=2779.58, theta=0.0,
        t_then=datetime(2026, 11, 6, 12, tzinfo=UTC), gamma_scale=1.0 / 1e-4,
    )
    assert out["vega_pnl_usd"] == pytest.approx(277.96, abs=0.5)


def test_spot_at_or_before_picks_the_latest_candle_opened_before_the_cutoff() -> None:
    cutoff = datetime(2026, 11, 5, 11, 30, tzinfo=UTC)
    ms = lambda h: int(datetime(2026, 11, 5, h, 0, tzinfo=UTC).timestamp() * 1000)  # noqa: E731
    bars = [
        {"t": ms(9), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.1209},
        {"t": ms(11), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.1221},   # opened 11:00 <= 11:30 -> this one
        {"t": ms(12), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.1230},   # after the cutoff
        {"t": "garbage"},
    ]
    assert pp.spot_at_or_before(bars, cutoff) == pytest.approx(1.1221)
    assert pp.spot_at_or_before(None, cutoff) is None
    assert pp.spot_at_or_before([{"t": ms(12), "c": 1.0}], cutoff) is None


@pytest.mark.asyncio
async def test_spot_fallbacks_read_latest_spot_and_the_1w_bars() -> None:
    import json

    cutoff = datetime(2026, 11, 5, 11, 30, tzinfo=UTC)
    t = int(datetime(2026, 11, 5, 11, 0, tzinfo=UTC).timestamp() * 1000)

    class _Redis:
        async def get(self, key: str):
            if key == "latest_spot:EURUSD":
                return "1.12377"
            if key == "bars:EURUSD:1W":
                return json.dumps([{"t": t, "o": 1, "h": 1, "l": 1, "c": 1.1221}])
            return None

    assert await pp._spot_fallbacks(_Redis(), cutoff) == (pytest.approx(1.12377), pytest.approx(1.1221))  # type: ignore[arg-type]
    assert await pp._spot_fallbacks(None, cutoff) == (None, None)
