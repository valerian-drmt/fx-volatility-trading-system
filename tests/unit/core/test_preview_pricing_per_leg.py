"""The preview prices each leg on its own forward and its own vol (#483).

It used to take the 1-month at-the-money strike as the forward of every
tenor, and the at-the-money vol for any strike typed on the ticket.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.trade_preview import (
    build_from_legs,
    compute_net_greeks,
    forward_for_tenor,
    iv_at_strike,
    price_structure,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)


def _tenor(fwd: float, atm: float) -> dict:
    return {
        "10dp": {"strike": round(fwd - 0.030, 4), "iv": atm + 0.012},
        "25dp": {"strike": round(fwd - 0.015, 4), "iv": atm + 0.005},
        "atm": {"strike": fwd, "iv": atm},
        "25dc": {"strike": round(fwd + 0.015, 4), "iv": atm + 0.002},
        "10dc": {"strike": round(fwd + 0.030, 4), "iv": atm + 0.006},
    }


SURFACE = {"1M": _tenor(1.1060, 0.055), "3M": _tenor(1.1120, 0.058)}


def test_each_tenor_has_its_own_forward() -> None:
    assert forward_for_tenor(SURFACE, "1M") == 1.1060
    assert forward_for_tenor(SURFACE, "3M") == 1.1120
    assert forward_for_tenor(SURFACE, "9M") == 1.1060, "an unknown tenor falls back to the front forward"


def test_the_vol_at_a_strike_follows_the_smile() -> None:
    assert iv_at_strike(SURFACE, "3M", 1.1120) == pytest.approx(0.058)
    # halfway between the 25-delta put and the money
    assert iv_at_strike(SURFACE, "3M", 1.1045) == pytest.approx(0.058 + 0.0025)
    assert iv_at_strike(SURFACE, "3M", 1.0820) == pytest.approx(0.070), "the put wing carries its skew"
    assert iv_at_strike(SURFACE, "3M", 0.9000) == pytest.approx(0.070), "flat beyond the last pillar"
    assert iv_at_strike(SURFACE, "3M", 1.5000) == pytest.approx(0.064)
    assert iv_at_strike({}, "3M", 1.10) is None


def _one_leg(strike: float | None, tenor: str = "3M", ct: str = "put"):
    spec = {"contract_type": ct, "side": "BUY", "tenor": tenor, "qty_factor": 1}
    if strike is not None:
        spec["strike"] = strike
    return build_from_legs([spec], SURFACE, as_of=NOW)


def test_a_typed_wing_strike_is_priced_at_its_own_vol() -> None:
    wing = _one_leg(1.0820).legs[0]
    assert wing.entry_iv_pct == pytest.approx(7.0), "not the 5.8 % of the money"
    atm = _one_leg(None).legs[0]
    assert atm.entry_iv_pct == pytest.approx(5.8)


def test_a_three_month_leg_is_priced_on_the_three_month_forward() -> None:
    put_3m = _one_leg(1.1120)
    greeks = compute_net_greeks(put_3m, SURFACE)
    # struck at the 3M forward: an at-the-money put, delta close to -0.5 x 125,000 x F
    assert greeks.delta_unhedged == pytest.approx(-0.5 * 125_000 * 1.1120, rel=0.04)
    # on the 1M forward (1.1060) the same put would be in the money and its delta larger
    on_front = compute_net_greeks(put_3m, {"1M": SURFACE["1M"], "3M": {**SURFACE["3M"], "atm": {"strike": 1.1060, "iv": 0.058}}})
    assert abs(on_front.delta_unhedged) > abs(greeks.delta_unhedged)
    assert price_structure(put_3m, SURFACE).total_premium_usd > 0
