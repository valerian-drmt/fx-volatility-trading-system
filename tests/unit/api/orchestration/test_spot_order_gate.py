"""The spot ticket passes the pre-trade checks (#491).

It used to reach the broker with none: up to 5,000,000 of base currency, at
market when no limit was given.
"""
from __future__ import annotations

from typing import Any

import pytest

from api.orchestration import pretrade_gate as gate_mod
from core.risk.pretrade import GateResult

pytestmark = pytest.mark.unit


def _fake_gate(book_delta: float, cap: float = 90_000.0, extra: list[str] | None = None):
    """A stand-in for the gate: it applies the delta limit to the book after
    the order, from the payload the spot gate builds."""
    seen: dict[str, Any] = {}

    async def _run(db: Any, redis: Any, *, payload: dict[str, Any], live_mode: bool, **_: Any) -> GateResult:
        seen["payload"], seen["live_mode"] = payload, live_mode
        post = book_delta + float(payload["greeks_net"]["delta_unhedged"])
        failures = list(extra or [])
        if abs(post) > cap:
            failures.append("cap_delta")
        return GateResult(
            passed=not failures, failures=failures,
            details={"book": {"delta_usd": book_delta}, "post_trade": {"delta_usd": post}, "caps": {"delta_usd": cap}},
        )

    return _run, seen


@pytest.fixture(autouse=True)
def _spot(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _s(redis: Any, payload: dict[str, Any]) -> float:
        return 1.10

    monkeypatch.setattr(gate_mod, "_spot", _s)


async def test_a_spot_order_is_judged_on_the_delta_it_adds(monkeypatch: pytest.MonkeyPatch) -> None:
    run, seen = _fake_gate(book_delta=0.0)
    monkeypatch.setattr(gate_mod, "run_pretrade_gate", run)
    ok = await gate_mod.run_spot_order_gate(None, None, side="SELL", qty_eur=22_533)
    assert ok.passed
    assert seen["payload"]["greeks_net"]["delta_unhedged"] == pytest.approx(-22_533 * 1.10)
    assert seen["payload"]["greeks_net"]["vega_usd_per_volpt"] == 0.0 and seen["live_mode"] is False
    assert seen["payload"]["structure"]["legs"][0]["qty"] * 125_000 == pytest.approx(22_533), "notional in EUR"

    big = await gate_mod.run_spot_order_gate(None, None, side="BUY", qty_eur=1_000_000)
    assert not big.passed and big.failures == ["cap_delta"]


async def test_an_order_that_reduces_the_delta_is_never_refused_by_the_delta_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # the book is long $500k of delta, over the limit: selling EUR brings it down
    run, _ = _fake_gate(book_delta=500_000.0)
    monkeypatch.setattr(gate_mod, "run_pretrade_gate", run)
    reduce = await gate_mod.run_spot_order_gate(None, None, side="SELL", qty_eur=100_000)
    assert reduce.passed, "still over the limit after it, but smaller: reducing must stay possible"
    add = await gate_mod.run_spot_order_gate(None, None, side="BUY", qty_eur=100_000)
    assert add.failures == ["cap_delta"]


async def test_the_other_checks_still_refuse_a_reducing_order(monkeypatch: pytest.MonkeyPatch) -> None:
    run, _ = _fake_gate(book_delta=500_000.0, extra=["trading_halted"])
    monkeypatch.setattr(gate_mod, "run_pretrade_gate", run)
    res = await gate_mod.run_spot_order_gate(None, None, side="SELL", qty_eur=100_000)
    assert not res.passed and res.failures == ["trading_halted"]
