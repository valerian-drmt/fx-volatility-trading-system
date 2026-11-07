"""``core.structures`` (#331): the four structures build their legs off a
published surface, price them on the kernel, sum greeks with the desk units,
and the signal -> structure mapping is unchanged by the move out of the
execution engine."""
from __future__ import annotations

import pytest

from core import structures as S
from core.pricing import bs
from core.vol.tenors import tenor_years

SURFACE = {
    "1M": {"atm": {"iv": 0.060, "strike": 1.1000}, "25dc": {"iv": 0.062, "strike": 1.1150}, "25dp": {"iv": 0.066, "strike": 1.0850}},
    "3M": {"atm": {"iv": 0.070, "strike": 1.1050}, "25dc": {"iv": 0.072, "strike": 1.1350}, "25dp": {"iv": 0.078, "strike": 1.0750}},
}
F = 1.1050


def test_straddle_legs_and_premium() -> None:
    legs = S.StraddleATM(tenor="3M", side="BUY", qty=5).legs(F, SURFACE)
    assert [(x.instrument, x.side, x.qty, x.strike) for x in legs] == [("CALL", "BUY", 5, 1.105), ("PUT", "BUY", 5, 1.105)]
    assert legs[0].premium_per_contract == bs.bs_price(F, 1.105, tenor_years("3M"), 0.070, "C")
    assert legs[0].premium_per_contract == pytest.approx(legs[1].premium_per_contract), "ATM straddle: call = put"


def test_risk_reversal_butterfly_calendar_sides() -> None:
    rr = S.RiskReversal25d(tenor="3M", direction="LONG_PUT").legs(F, SURFACE)
    assert [(x.instrument, x.side) for x in rr] == [("CALL", "SELL"), ("PUT", "BUY")]
    fly = S.Butterfly25d(tenor="3M", side="SELL", qty=2).legs(F, SURFACE)
    assert [(x.instrument, x.side, x.qty) for x in fly] == [("CALL", "SELL", 2), ("PUT", "SELL", 2), ("CALL", "BUY", 4)]
    cal = S.CalendarSpread(tenor_near="1M", tenor_far="3M", side="BUY").legs(F, SURFACE)
    assert [(x.tenor, x.side) for x in cal] == [("1M", "SELL"), ("3M", "BUY")]


def test_missing_pillar_or_unknown_tenor_gives_no_legs() -> None:
    assert S.StraddleATM(tenor="6M").legs(F, SURFACE) == []
    assert S.RiskReversal25d(tenor="1M").legs(F, {"1M": {"atm": {"iv": 0.06, "strike": 1.1}}}) == []
    assert S.StraddleATM(tenor="7M").legs(F, {"7M": {"atm": {"iv": 0.06, "strike": 1.1}}}) == []


def test_net_greeks_use_desk_units_and_sign() -> None:
    long_ = S.StraddleATM(tenor="3M", side="BUY", qty=1).net_greeks(F, SURFACE)
    short = S.StraddleATM(tenor="3M", side="SELL", qty=1).net_greeks(F, SURFACE)
    T = tenor_years("3M")
    assert long_.vega == pytest.approx(2 * bs.bs_vega(F, 1.105, T, 0.07) / 100.0), "vega per vol point"
    assert long_.theta == pytest.approx(2 * bs.bs_theta(F, 1.105, T, 0.07, "C")), "theta per day"
    assert long_.gamma == pytest.approx(2 * bs.bs_gamma(F, 1.105, T, 0.07))
    assert long_.delta == pytest.approx(bs.bs_delta(F, 1.105, T, 0.07, "C") + bs.bs_delta(F, 1.105, T, 0.07, "P"))
    assert short.vega == pytest.approx(-long_.vega) and short.theta == pytest.approx(-long_.theta)
    fut = S.sum_greeks([S.Leg("FUT", "SELL", 3, None, "3M", None, 0.0)], F)
    assert fut.delta == -3.0 and fut.vega == 0.0


def test_signal_to_structure_mapping() -> None:
    assert isinstance(S.signal_to_structure("level", "3M", "CHEAP"), S.StraddleATM)
    assert S.signal_to_structure("level", "3M", "EXPENSIVE").side == "SELL"
    cal = S.signal_to_structure("term_slope", "6M")
    assert isinstance(cal, S.CalendarSpread) and (cal.tenor_near, cal.tenor_far) == ("1M", "6M")
    assert isinstance(S.signal_to_structure("smile", "3M"), S.Butterfly25d)
    assert S.signal_to_structure("skew", "3M", "EXPENSIVE").direction == "LONG_PUT"
    assert S.signal_to_structure("unknown") is None


def test_execution_engine_module_is_gone_and_api_imports_core() -> None:
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("engines.execution.structures")
    from pathlib import Path
    src = Path("src/api/routers/cockpit.py").read_text(encoding="utf-8")
    assert "from core import structures as S" in src and "1.17" not in src
    lint = Path(".importlinter").read_text(encoding="utf-8")
    assert "ignore_imports" not in lint
