"""Tests for engines.execution.structures — BS pricing + legs + net greeks."""
from __future__ import annotations

import pytest


def _surface():
    return {
        "1M": {
            "atm": {"iv": 0.06, "strike": 1.17},
            "25dc": {"iv": 0.062, "strike": 1.19},
            "25dp": {"iv": 0.063, "strike": 1.15},
            "10dc": {"iv": 0.068, "strike": 1.22},
            "10dp": {"iv": 0.070, "strike": 1.13},
        },
        "3M": {
            "atm": {"iv": 0.062, "strike": 1.17},
            "25dc": {"iv": 0.064, "strike": 1.20},
            "25dp": {"iv": 0.065, "strike": 1.14},
        },
    }


def test_bs_price_call_put_parity() -> None:
    from engines.execution.structures import bs_price

    F, K, T, sigma = 1.17, 1.17, 1 / 12, 0.06
    c = bs_price(F, K, T, sigma, "CALL")
    p = bs_price(F, K, T, sigma, "PUT")
    # Futures option parity : C - P = F - K. At ATM (F=K) parity => C = P.
    assert c == pytest.approx(p, abs=1e-6)


def test_bs_greeks_atm_call_has_positive_vega_gamma_and_negative_theta() -> None:
    from engines.execution.structures import bs_greeks

    g = bs_greeks(1.17, 1.17, 1 / 12, 0.06, "CALL")
    assert g["vega"] > 0
    assert g["gamma"] > 0
    assert g["theta"] < 0
    assert 0.4 < g["delta"] < 0.6


def test_straddle_atm_has_zero_delta() -> None:
    from engines.execution.structures import StraddleATM

    s = StraddleATM(tenor="1M", qty=10)
    legs = s.legs(1.17, _surface())
    assert len(legs) >= 2
    net = s.net_greeks(1.17, _surface())
    assert abs(net.delta) < 0.5      # ATM straddle is approximately delta-neutral
    assert net.vega > 0              # long straddle → long vega
    assert net.gamma > 0              # long gamma
    assert net.theta < 0              # paying theta


def test_risk_reversal_long_call_has_positive_delta() -> None:
    from engines.execution.structures import RiskReversal25d

    rr = RiskReversal25d(tenor="1M", direction="LONG_CALL", qty=10)
    net = rr.net_greeks(1.17, _surface())
    assert net.delta > 0      # long call + short put = long delta on both sides


def test_butterfly_has_lower_vega_magnitude_than_atm_alone() -> None:
    from engines.execution.structures import Butterfly25d, StraddleATM

    bf = Butterfly25d(tenor="1M", qty=10)
    straddle = StraddleATM(tenor="1M", qty=10)
    assert abs(bf.net_greeks(1.17, _surface()).vega) < abs(
        straddle.net_greeks(1.17, _surface()).vega
    )


def test_calendar_spread_goes_long_far_short_near_when_buy() -> None:
    from engines.execution.structures import CalendarSpread

    cal = CalendarSpread(tenor_near="1M", tenor_far="3M", side="BUY", qty=10)
    legs = cal.legs(1.17, _surface())
    assert len(legs) == 2
    near = next(leg for leg in legs if leg.tenor == "1M")
    far = next(leg for leg in legs if leg.tenor == "3M")
    assert near.side == "SELL"
    assert far.side == "BUY"


def test_signal_to_structure_maps_pc_labels_to_structures() -> None:
    from engines.execution.structures import (
        Butterfly25d,
        CalendarSpread,
        RiskReversal25d,
        StraddleATM,
        signal_to_structure,
    )

    assert isinstance(signal_to_structure("level", "3M"), StraddleATM)
    assert isinstance(signal_to_structure("term_slope", "6M"), CalendarSpread)
    assert isinstance(signal_to_structure("smile", "1M"), Butterfly25d)
    assert isinstance(signal_to_structure("skew", "1M"), RiskReversal25d)
    assert signal_to_structure("unknown", "1M") is None


def test_signal_to_structure_direction_flips_side() -> None:
    from engines.execution.structures import signal_to_structure

    buy_s = signal_to_structure("level", "1M", direction="CHEAP")
    sell_s = signal_to_structure("level", "1M", direction="EXPENSIVE")
    assert buy_s.side == "BUY"
    assert sell_s.side == "SELL"
