"""Unit tests for core.positions.position_pricing."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from core.positions.position_pricing import LegSpec, price_position


def _flat_surface(iv_decimal: float = 0.07) -> dict:
    """ATM-only flat surface in the PUBLISHED nested shape (#310):
    ``{tenor: {label: {iv, strike}}}`` with decimal IVs."""
    cell = {"atm": {"iv": iv_decimal, "strike": 1.0850, "source": "listed"}}
    return {"1M": dict(cell), "3M": dict(cell), "6M": dict(cell)}


def test_price_position_long_call():
    """Long call : positive vega + positive delta."""
    legs = [LegSpec(
        leg_idx=0, contract_type="call", strike=1.0850,
        expiry=date(2026, 8, 4), tenor="3M", side="BUY", qty=10,
    )]
    surface = _flat_surface(0.07)
    out = price_position(
        legs=legs, surface=surface, spot=1.0850,
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )
    assert out.mark_value_usd > 0
    assert out.total_delta > 0          # long call → positive delta
    assert out.total_vega_usd_per_volpt > 0
    assert out.total_gamma_usd_per_pip2 > 0
    assert out.n_surface_missing == 0


def test_price_position_short_put_inverts_delta():
    legs = [LegSpec(
        leg_idx=0, contract_type="put", strike=1.0850,
        expiry=date(2026, 8, 4), tenor="3M", side="SELL", qty=5,
    )]
    out = price_position(
        legs=legs, surface=_flat_surface(0.07), spot=1.0850,
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )
    # Short put : delta = -(put_delta < 0) → positive ; vega negative
    assert out.total_delta > 0
    assert out.total_vega_usd_per_volpt < 0


def test_price_position_long_straddle_delta_near_zero():
    """ATM long straddle : delta cancels, vega doubles, gamma doubles."""
    legs = [
        LegSpec(leg_idx=0, contract_type="call", strike=1.0850,
                expiry=date(2026, 8, 4), tenor="3M", side="BUY", qty=10),
        LegSpec(leg_idx=1, contract_type="put", strike=1.0850,
                expiry=date(2026, 8, 4), tenor="3M", side="BUY", qty=10),
    ]
    out = price_position(
        legs=legs, surface=_flat_surface(0.07), spot=1.0850,
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )
    assert abs(out.total_delta) < 0.5
    assert out.total_vega_usd_per_volpt > 0


def test_price_position_falls_back_to_entry_iv():
    """Surface missing → use fallback_iv ; n_surface_missing flagged."""
    legs = [LegSpec(
        leg_idx=0, contract_type="call", strike=1.0850,
        expiry=date(2026, 8, 4), tenor="3M", side="BUY", qty=10,
        fallback_iv=0.08,
    )]
    out = price_position(
        legs=legs, surface=None, spot=1.0850,
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )
    assert out.mark_value_usd > 0
    assert out.n_surface_missing == 1
    assert out.legs[0].iv_used == pytest.approx(0.08)


def test_price_position_zero_when_no_iv_available():
    legs = [LegSpec(
        leg_idx=0, contract_type="call", strike=1.0850,
        expiry=date(2026, 8, 4), tenor="3M", side="BUY", qty=10,
        fallback_iv=None,
    )]
    out = price_position(
        legs=legs, surface=None, spot=1.0850,
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )
    assert out.mark_value_usd == 0
    assert out.legs[0].iv_used == 0
