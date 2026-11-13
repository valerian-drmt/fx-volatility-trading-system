"""A structure and its delta hedge are one package (#481, #482).

Prod, 2026-11-13: BUY 40 puts with the hedge box ticked. The hedge was a
second order sent by the browser, so the gate judged the puts unhedged
(delta -$2.58M against a $90.6k limit) and could never reach the hedge.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from api.orchestration.pretrade_gate import budget_from_gate, max_qty_within_limits, trade_greeks_from_preview
from core.execution.package_hedge import hedge_target, plan_hedge, underlying_future_month
from core.risk.pretrade import BookGreeks, Caps, GateInputs, TradeGreeks, evaluate_pretrade
from engines.execution.package_hedge import hedge_after_fill
from persistence.models import StructureOrder, TradeEvent, TradePreviewRow, TradeStructure

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 10, 0, tzinfo=UTC)
TODAY = NOW.date()
SPOT = 1.106


# ── pure rules ───────────────────────────────────────────────────────────────

def test_the_hedge_of_the_refused_order_and_what_is_left() -> None:
    plan = plan_hedge(-2_575_628.94, SPOT)
    assert plan is not None and (plan.side, plan.qty) == ("BUY", 19)
    assert abs(plan.residual_delta_usd) < plan.delta_per_future_usd / 2 + 1, "within half a future of flat"
    assert plan.residual_delta_usd == pytest.approx(-2_575_628.94 + 19 * 125_000 * SPOT, abs=0.01)


def test_a_long_delta_is_hedged_by_selling_and_a_tiny_one_is_not_hedged() -> None:
    assert plan_hedge(700_000.0, SPOT).side == "SELL"
    assert plan_hedge(30_000.0, SPOT) is None, "less than half a future"
    assert plan_hedge(700_000.0, 0.0) is None, "no spot, no hedge"


def test_the_hedge_follows_the_fills() -> None:
    assert hedge_target(19, 0, 40) == 0, "nothing before a fill"
    assert hedge_target(19, 10, 40) == 5
    assert hedge_target(19, 40, 40) == 19
    assert hedge_target(19, 45, 40) == 19, "never more than the plan"
    assert hedge_target(0, 40, 40) == 0


def test_the_future_behind_an_option() -> None:
    assert underlying_future_month("EUUG7 P1105", TODAY) == "202703"   # February option -> March future
    assert underlying_future_month("EUUZ6 C1210", TODAY) == "202612"
    assert underlying_future_month("EUUF7 C1150", TODAY) == "202703"
    assert underlying_future_month("6EH7", TODAY) is None


# ── the gate judges the package ──────────────────────────────────────────────

def _payload(hedged: bool) -> dict[str, Any]:
    plan = plan_hedge(-2_575_628.94, SPOT)
    return {
        "greeks_net": {"delta_unhedged": -2_575_628.94, "vega_usd_per_volpt": 10_927.32, "gamma_usd_per_pip2": 0.58756},
        "structure": {"legs": [{"contract_type": "put", "qty": 40}]},
        "sizing": {"base_qty": 40},
        "hedge": plan.as_dict() if hedged else None,
    }


def test_the_gate_takes_the_residual_delta_of_a_hedged_package() -> None:
    unhedged = trade_greeks_from_preview(_payload(False), SPOT)
    hedged = trade_greeks_from_preview(_payload(True), SPOT)
    assert unhedged.delta_usd == pytest.approx(-2_575_628.94)
    assert abs(hedged.delta_usd) < 70_000 and hedged.vega_usd_per_volpt == unhedged.vega_usd_per_volpt


_OK = GateInputs(
    halted=False, halt_reason=None, live_mode=False, risk_heartbeat_age_s=None, max_risk_heartbeat_age_s=30.0,
    ib_account_type=None, required_account_type="paper",
    book=BookGreeks(0.0, 0.0, 0.0, 0, 0),
    trade=TradeGreeks(delta_usd=10_000.0, vega_usd_per_volpt=1_000.0, gamma_usd_per_pip=500.0, notional_eur=500_000.0),
    caps=Caps(delta_usd=90_000.0, vega_usd_per_volpt=1_888.0, gamma_usd_per_pip=1_018.0, max_notional_eur=5_000_000.0),
    symbols=("EUR",), allowed_symbols=("EUR",), surface_age_s=None, max_surface_age_s=270.0, arb_ok=None,
)


def test_two_levels_per_limit() -> None:
    assert evaluate_pretrade(_OK).passed and evaluate_pretrade(_OK).warnings == []
    # 85 % of the vega limit: allowed only with a confirmation
    warn = replace(_OK, trade=replace(_OK.trade, vega_usd_per_volpt=1_600.0))
    res = evaluate_pretrade(warn)
    assert not res.passed and res.failures == ["limits_need_confirmation"] and res.warnings == ["warn_vega"]
    confirmed = evaluate_pretrade(replace(warn, confirmed=True))
    assert confirmed.passed and confirmed.warnings == ["warn_vega"]
    # over the limit: refused, confirmed or not
    over = replace(_OK, trade=replace(_OK.trade, vega_usd_per_volpt=2_000.0), confirmed=True)
    assert evaluate_pretrade(over).failures == ["cap_vega"]


def test_the_largest_size_that_fits() -> None:
    # 40 contracts: vega 10,927 for a limit of 1,888 -> 6 contracts fit (273 each)
    result = evaluate_pretrade(replace(
        _OK, confirmed=True,
        trade=TradeGreeks(delta_usd=-30_000.0, vega_usd_per_volpt=10_927.32, gamma_usd_per_pip=5_875.6,
                          notional_eur=5_000_000.0),
        caps=Caps(delta_usd=90_645.12, vega_usd_per_volpt=1_888.44, gamma_usd_per_pip=1_018.33,
                  max_notional_eur=5_000_000.0),
    ))
    budget = budget_from_gate(result, qty=40, hedged=True)
    assert budget["max_qty"] == 6
    assert {r["key"]: r["breached"] for r in budget["rows"]} == {"delta": False, "vega": True, "gamma": True, "notional": False}
    # a book already long vega leaves less room for more
    rows = [{"key": "vega", "cap": 1_888.0, "trade": 2_730.0, "before": 1_000.0}]
    assert max_qty_within_limits(rows, 10) == 3
    # a trade that reduces the book's vega is not limited by it on that side
    rows = [{"key": "vega", "cap": 1_888.0, "trade": -2_730.0, "before": 1_000.0}]
    assert max_qty_within_limits(rows, 10) == 10


# ── the engine hedges on fill ────────────────────────────────────────────────

class _Executor:
    def __init__(self, fail: bool = False) -> None:
        self.placed: list[Any] = []
        self.fail = fail

    async def place_order_trade(self, req: Any, *, order_ref: str | None = None) -> Any:
        if self.fail:
            raise ValueError("Contract not qualified by IB")
        self.placed.append((req, order_ref))

        class _Event:
            def __iadd__(self, _h: Any) -> Any:
                return self

        return SimpleNamespace(
            order=SimpleNamespace(orderId=900 + len(self.placed), permId=7000 + len(self.placed)),
            orderStatus=SimpleNamespace(status="Submitted", whyHeld=""), log=[],
            statusEvent=_Event(), fillEvent=_Event(), commissionReportEvent=_Event(),
        )


async def _package(db: Any, *, hedge: bool = True, filled: int = 0) -> tuple[int, int]:
    plan = plan_hedge(-2_575_628.94, SPOT)
    db.add(TradePreviewRow(
        preview_id="tp_pkg", expires_at=NOW + timedelta(minutes=2), structure_type="vanilla_put",
        reference_tenor="3M", state="submitted", pre_submit_checks=[],
        structure_full_payload={"hedge": plan.as_dict() if hedge else None},
    ))
    s = TradeStructure(preview_id="tp_pkg", structure_type="vanilla_put", reference_tenor="3M", base_qty=40,
                       state="submitted", execution_mode="live")
    db.add(s)
    await db.flush()
    o = StructureOrder(structure_id=s.id, leg_idx=0, order_role="entry", contract_type="put", side="BUY", qty=40,
                       order_type="LMT", limit_price=0.016, state="partially_filled" if filled < 40 else "filled",
                       qty_filled=filled, ib_local_symbol="EUUG7 P1105", contract_expiry=date(2027, 2, 5))
    db.add(o)
    await db.commit()
    return int(s.id), int(o.id)


async def _hedges(db: Any, sid: int) -> list[StructureOrder]:
    return list((await db.execute(
        select(StructureOrder).where(StructureOrder.structure_id == sid, StructureOrder.order_role == "hedge")
        .order_by(StructureOrder.leg_idx)
    )).scalars().all())


async def test_no_future_before_a_fill_then_the_hedge_follows(sqlite_sessionmaker: Any) -> None:
    ex = _Executor()
    async with sqlite_sessionmaker() as db:
        sid, oid = await _package(db, filled=0)
    out = await hedge_after_fill(sqlite_sessionmaker, ex, oid, now=NOW)
    assert out["placed"] == 0 and ex.placed == [], "unfilled options never leave naked futures"

    async with sqlite_sessionmaker() as db:
        (await db.get(StructureOrder, oid)).qty_filled = 10
        await db.commit()
    out = await hedge_after_fill(sqlite_sessionmaker, ex, oid, now=NOW)
    assert out["placed"] == 5
    req, ref = ex.placed[0]
    assert (req.sec_type, req.symbol, req.side, req.qty, req.expiry, req.limit_price) == ("FUT", "EUR", "BUY", 5, "202703", None)
    async with sqlite_sessionmaker() as db:
        h = await _hedges(db, sid)
        assert [(x.qty, x.state, x.leg_idx, x.contract_type) for x in h] == [(5, "submitted", 100, "future")]
        assert ref == f"fxvol:{sid}:{h[0].id}"
    # the same fill seen twice places nothing more
    assert (await hedge_after_fill(sqlite_sessionmaker, ex, oid, now=NOW))["placed"] == 0

    async with sqlite_sessionmaker() as db:
        (await db.get(StructureOrder, oid)).qty_filled = 40
        await db.commit()
    assert (await hedge_after_fill(sqlite_sessionmaker, ex, oid, now=NOW))["placed"] == 14
    async with sqlite_sessionmaker() as db:
        assert sum(x.qty for x in await _hedges(db, sid)) == 19, "the whole plan, no more"


async def test_a_hedge_that_cannot_be_placed_is_visible(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        sid, oid = await _package(db, filled=40)
    out = await hedge_after_fill(sqlite_sessionmaker, _Executor(fail=True), oid, now=NOW)
    assert out["placed"] == 0 and "not qualified" in out["error"]
    async with sqlite_sessionmaker() as db:
        h = await _hedges(db, sid)
        assert h[0].state == "rejected" and "hedge not placed" in h[0].rejection_text
        ev = (await db.execute(select(TradeEvent).where(TradeEvent.event_type == "package_hedge_failed"))).scalars().all()
        assert len(ev) == 1 and ev[0].severity == "critical"
    # the failed order does not count as hedge in place: the next fill event retries
    ok = _Executor()
    assert (await hedge_after_fill(sqlite_sessionmaker, ok, oid, now=NOW))["placed"] == 19


async def test_a_trade_without_a_hedge_or_a_future_leg_is_left_alone(sqlite_sessionmaker: Any) -> None:
    ex = _Executor()
    async with sqlite_sessionmaker() as db:
        _, oid = await _package(db, hedge=False, filled=40)
    assert await hedge_after_fill(sqlite_sessionmaker, ex, oid, now=NOW) is None and ex.placed == []


def test_a_fat_finger_size_is_refused_whatever_the_book() -> None:
    """#481: one order of more contracts than the per-order limit is refused
    before any risk arithmetic."""
    big = replace(_OK, order_contracts=500.0, max_order_contracts=200.0)
    res = evaluate_pretrade(big)
    assert "cap_contracts" in res.failures and res.details["max_order_contracts"] == 200.0
    assert evaluate_pretrade(replace(_OK, order_contracts=40.0, max_order_contracts=200.0)).passed
    assert evaluate_pretrade(replace(_OK, order_contracts=500.0, max_order_contracts=None)).passed, "limit off"


class _Reporting(_Executor):
    def is_connected(self) -> bool:
        return True

    def account_is_reporting(self) -> bool:
        return True


async def test_a_failed_hedge_is_retried_without_another_fill(sqlite_sessionmaker: Any) -> None:
    """#482: a fully filled trade has no further fill to wait for. The sweep
    brings its hedge to where the fills say it should be."""
    from engines.execution.package_hedge import hedge_sweep

    async with sqlite_sessionmaker() as db:
        sid, oid = await _package(db, filled=40)
        (await db.get(TradeStructure, sid)).created_at = NOW
        await db.commit()
    failed = _Reporting(fail=True)
    assert (await hedge_after_fill(sqlite_sessionmaker, failed, oid, now=NOW))["placed"] == 0

    ok = _Reporting()
    out = await hedge_sweep(sqlite_sessionmaker, ok, now=NOW)
    assert out == {"trades": 1, "placed": 19}
    assert (await hedge_sweep(sqlite_sessionmaker, ok, now=NOW))["placed"] == 0, "nothing more once it is on"

    class _Dead(_Executor):
        def is_connected(self) -> bool:
            return True

        def account_is_reporting(self) -> bool:
            return False

    assert await hedge_sweep(sqlite_sessionmaker, _Dead(), now=NOW) == {"skipped": "account_not_reporting"}


def test_the_features_that_are_not_ready_are_off() -> None:
    """#493: auto-exit would strand positions in `closing` and the monitor's
    hedges have no expiry, no status handling and no line in the blotter.
    Both must stay off until they are finished or removed."""
    import inspect

    from api.config import Settings
    from api.orchestration.position_monitor import PositionMonitorScheduler

    assert Settings.model_fields["EXIT_AUTO_EXECUTE_ENABLED"].default == "false"
    assert inspect.signature(PositionMonitorScheduler.__init__).parameters["execute_hedge"].default is False
