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


def test_price_position_flags_an_unpriceable_leg_instead_of_marking_it_zero():
    # Audit R3-01 shape (07#9): this test used to assert ``mark_value_usd == 0``
    # for a real position with no IV, i.e. "no data" rendered as "worth
    # nothing". The mark is now reported as incomplete: the caller (the
    # position monitor) keeps its fallback path instead of booking a zero.
    legs = [LegSpec(
        leg_idx=0, contract_type="call", strike=1.0850,
        expiry=date(2026, 8, 4), tenor="3M", side="BUY", qty=10,
        fallback_iv=None,
    )]
    out = price_position(
        legs=legs, surface=None, spot=1.0850,
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )
    assert out.n_unpriced == 1 and out.n_surface_missing == 1
    assert out.complete is False
    assert out.legs[0].iv_source == "none" and out.legs[0].iv_used == 0
    assert out.legs[0].qty_signed == 0, "an unpriced leg contributes no greeks"
    assert out.mark_value_usd == 0, "nothing priced => nothing summed (not a market value)"


def test_price_position_is_complete_when_every_leg_has_an_iv():
    legs = [LegSpec(
        leg_idx=0, contract_type="call", strike=1.0850,
        expiry=date(2026, 8, 4), tenor="3M", side="BUY", qty=10,
        fallback_iv=0.08,
    )]
    out = price_position(
        legs=legs, surface=None, spot=1.0850,
        now=datetime(2026, 5, 4, tzinfo=UTC),
    )
    assert out.complete is True and out.n_unpriced == 0
    assert out.n_surface_missing == 1 and out.legs[0].iv_source == "fallback"
