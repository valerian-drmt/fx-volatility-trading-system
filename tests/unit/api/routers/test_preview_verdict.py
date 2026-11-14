"""#483: what the preview says is what the trade is, and what the submit
will do. Drives the route itself, on the sandbox surface."""
from __future__ import annotations

from typing import Any

import pytest

from core.trade_preview import COMMISSION_USD

pytestmark = pytest.mark.unit


async def _preview(monkeypatch: Any, sqlite_sessionmaker: Any, *, qty: int, hedge: bool = False,
                   budget: Any = None) -> dict[str, Any]:
    from api.routers import trade as trade_mod
    from api.routers.trade import LegSpec, PreviewRequest, create_preview

    monkeypatch.setattr(trade_mod, "get_redis_client_or_none", lambda: None)
    if budget is not None:
        monkeypatch.setattr(trade_mod, "preview_risk_budget", budget)
    req = PreviewRequest(qty=qty, hedge=hedge, legs=[
        LegSpec(contract_type="put", side="BUY", tenor="3M", delta_pillar="25dp"),
    ])
    async with sqlite_sessionmaker() as db:
        return await create_preview(req, db, symbol="EURUSD")


async def _clean_budget(*_a: Any, **_k: Any) -> dict[str, Any]:
    return {"failures": [], "rows": []}


async def test_premium_cost_and_greeks_are_those_of_the_whole_order(monkeypatch: Any, sqlite_sessionmaker: Any) -> None:
    """A 22-lot showed 769.32 of premium on prod: the price of one contract.
    The funds check compared that with the account."""
    one = await _preview(monkeypatch, sqlite_sessionmaker, qty=1, budget=_clean_budget)
    many = await _preview(monkeypatch, sqlite_sessionmaker, qty=22, budget=_clean_budget)
    unit = one["pricing"]["premium_paid_usd"]
    assert unit > 0
    assert many["pricing"]["premium_per_unit_usd"] == pytest.approx(unit, rel=1e-9)
    assert many["pricing"]["premium_paid_usd"] == pytest.approx(22 * unit, rel=1e-6)
    assert many["sizing"]["final_premium_usd"] == pytest.approx(22 * unit, abs=0.01)
    assert many["costs"]["commission_usd"] == pytest.approx(22 * COMMISSION_USD["option"])
    assert many["costs"]["total_trade_cost_usd"] == pytest.approx(22 * unit + 22 * COMMISSION_USD["option"], abs=0.02)
    assert many["greeks_net"]["vega_usd_per_volpt"] == pytest.approx(
        22 * one["greeks_net"]["vega_usd_per_volpt"], rel=1e-4)
    assert many["pricing"]["max_loss_usd"] == pytest.approx(22 * one["pricing"]["max_loss_usd"], rel=1e-6)


async def test_valid_for_submit_means_the_submit_would_accept(monkeypatch: Any, sqlite_sessionmaker: Any) -> None:
    """The state was decided by the preview's own checks while the gate's
    refusals sat beside it: a preview read valid_for_submit with blockers."""
    async def refusing(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"failures": ["trading_halted"], "rows": []}

    ok = await _preview(monkeypatch, sqlite_sessionmaker, qty=1, budget=_clean_budget)
    assert ok["submit_blockers"] in ([], ["surface_stale"])
    assert (ok["state"] == "valid_for_submit") == (ok["submit_blockers"] == [])

    blocked = await _preview(monkeypatch, sqlite_sessionmaker, qty=1, budget=refusing)
    assert "trading_halted" in blocked["submit_blockers"]
    assert blocked["state"] == "blocked"


async def test_a_gate_that_cannot_be_asked_is_not_a_pass(monkeypatch: Any, sqlite_sessionmaker: Any) -> None:
    async def broken(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise RuntimeError("no account snapshot")

    p = await _preview(monkeypatch, sqlite_sessionmaker, qty=1, budget=broken)
    assert "gate_unavailable" in p["submit_blockers"] and p["state"] == "blocked"


async def test_no_check_is_a_constant(monkeypatch: Any, sqlite_sessionmaker: Any) -> None:
    """The sandbox surface carries no fit: the arbitrage check is then not
    evaluated. It used to read "passed" from a hardcoded False, like the
    liquidity check from a hardcoded 99."""
    p = await _preview(monkeypatch, sqlite_sessionmaker, qty=1, budget=_clean_budget)
    checks = {c["name"]: c for c in p["pre_submit_checks"]}
    for name in ("no_arb_violation_on_legs", "minimum_liquidity"):
        assert checks[name]["passed"] is None and checks[name]["evaluated"] is False
        assert checks[name]["blocking"] is False
    assert "vega_under_book_limit" not in checks
    assert p["surface_meta"] == {"butterfly_ok": None, "calendar_ok": None}


async def test_the_hedge_commission_comes_from_the_server_table(monkeypatch: Any, sqlite_sessionmaker: Any) -> None:
    p = await _preview(monkeypatch, sqlite_sessionmaker, qty=50, hedge=True, budget=_clean_budget)
    hedge = p["hedge"]
    assert hedge is not None and hedge["qty"] > 0
    assert p["costs"]["hedge_commission_usd"] == pytest.approx(hedge["qty"] * COMMISSION_USD["full"])
    assert p["costs"]["commission_per_contract_usd"] == COMMISSION_USD["option"]
