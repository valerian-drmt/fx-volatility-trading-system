"""Unit tests for the pure stress-reval core (core.risk.stress)."""
from __future__ import annotations

from core.pricing.bs import bs_delta, bs_price, bs_vega
from core.risk.stress import reval_book

SPOT = 1.10


def _opt(K: float, right: str, *, qty: float = 1.0, side: float = 1.0, T: float = 0.25, iv: float = 0.08) -> dict:
    return {
        "type": "OPTION", "qty_signed": qty * side, "mult": 100_000.0,
        "K": K, "T": T, "iv": iv, "right": right,
        "price_base": bs_price(SPOT, K, T, iv, right),
    }


def _future(qty: float = 1.0, side: float = 1.0) -> dict:
    return {"type": "FUTURE", "qty_signed": qty * side, "mult": 125_000.0}


def test_zero_shock_pnl_is_zero():
    book = [_opt(1.10, "C"), _opt(1.08, "P", side=-1.0)]
    assert reval_book(book, SPOT, output="pnl") == 0.0


def test_spot_shock_matches_delta_first_order():
    p = _opt(1.10, "C")
    pnl = reval_book([p], SPOT, dspot_bp=1.0, output="pnl")
    dspot = SPOT * 1e-4
    expected = p["qty_signed"] * p["mult"] * bs_delta(SPOT, 1.10, 0.25, 0.08, "C") * dspot
    assert pnl == _approx(expected, rel=0.02)


def test_parallel_vol_shock_matches_vega():
    p = _opt(1.10, "C")
    pnl = reval_book([p], SPOT, dvol_vp=1.0, output="pnl")  # +1 vol point
    expected = p["qty_signed"] * p["mult"] * bs_vega(SPOT, 1.10, 0.25, 0.08) * 0.01
    assert pnl == _approx(expected, rel=0.02)


def test_time_decay_loses_for_long_option():
    # A long ATM option bleeds value as T shrinks (theta < 0).
    pnl = reval_book([_opt(1.10, "C")], SPOT, dt_days=5.0, output="pnl")
    assert pnl < 0


def test_delta_output_at_base():
    p = _opt(1.10, "C")
    out = reval_book([p], SPOT, output="delta")
    expected = p["qty_signed"] * p["mult"] * bs_delta(SPOT, 1.10, 0.25, 0.08, "C")
    assert out == _approx(expected, rel=1e-9)


def test_skew_shock_enriches_call_wing_cheapens_put_wing():
    # +ΔRR : OTM call (K>spot) IV up → long call gains ; OTM put (K<spot) IV down.
    call_wing = reval_book([_opt(1.16, "C")], SPOT, dskew_vp=2.0, output="pnl")
    put_wing = reval_book([_opt(1.04, "P")], SPOT, dskew_vp=2.0, output="pnl")
    assert call_wing > 0   # long OTM call richer
    assert put_wing < 0    # long OTM put cheaper


def test_fly_shock_lifts_wings():
    # +ΔBF : both wings richer vs ATM → long wing gains, ATM ~flat.
    wing = reval_book([_opt(1.16, "C")], SPOT, dfly_vp=2.0, output="pnl")
    atm = reval_book([_opt(1.10, "C")], SPOT, dfly_vp=2.0, output="pnl")
    assert wing > 0
    assert abs(atm) < abs(wing)


def test_future_pnl_and_delta():
    f = _future(qty=2.0)
    pnl = reval_book([f], SPOT, dspot_bp=100.0, output="pnl")
    expected_pnl = f["qty_signed"] * f["mult"] * (SPOT * 1.01 - SPOT)
    assert pnl == _approx(expected_pnl, rel=1e-9)
    assert reval_book([f], SPOT, output="delta") == f["qty_signed"] * f["mult"]
    assert reval_book([f], SPOT, output="vega") == 0.0  # futures carry no vega


def _approx(x: float, rel: float):
    import pytest
    return pytest.approx(x, rel=rel)
