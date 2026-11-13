"""Stage 4 of the order-lifecycle epic: the order path holds by construction.

#489 one function records that an order ended, with everything that follows;
#490 a half-done placement is finished or pulled back, never left;
#491 every order passes through the same door.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from engines.execution.manual_tickets import book_manual_ticket
from engines.execution.order_reconciler import reconcile_stuck_orders
from engines.execution.reaper import reap_stale_orders
from persistence.models import (
    BookedPosition,
    LegPosition,
    StructureFill,
    StructureOrder,
    TradeEvent,
    TradeStructure,
)
from persistence.order_state import end_order, finalise_structure_if_done
from persistence.reservation import recompute_reservation

pytestmark = pytest.mark.unit

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[4]


async def _trade(db: Any, state: str = "submitted") -> TradeStructure:
    s = TradeStructure(structure_type="vanilla_call", reference_tenor="1M", base_qty=10, state=state,
                       execution_mode="live")
    db.add(s)
    await db.flush()
    return s


async def _order(db: Any, s: TradeStructure, *, role: str = "entry", state: str = "submitted", idx: int = 0,
                 closes: int | None = None, filled: int = 0, ib_id: str | None = "7", age_s: float = 0) -> StructureOrder:
    o = StructureOrder(
        structure_id=s.id, leg_idx=idx, order_role=role, contract_type="call",
        side="BUY" if role == "entry" else "SELL", qty=10, order_type="LMT", limit_price=0.01, state=state,
        ib_local_symbol="EUUG7 C1110", closes_order_id=closes, qty_filled=filled, ib_order_id=ib_id,
        submitted_at=(NOW - timedelta(seconds=age_s)) if state != "pending" else None,
        state_updated_at=NOW - timedelta(seconds=age_s),
    )
    db.add(o)
    await db.flush()
    return o


# ── #489: one function for "this order ended" ────────────────────────────────

async def test_an_order_ib_rejects_finalises_its_trade_and_frees_the_leg(sqlite_sessionmaker: Any) -> None:
    """Prod had eleven trades stuck in `submitted` whose only order was
    cancelled or rejected: the status handler updated the order and nothing
    updated the trade."""
    async with sqlite_sessionmaker() as db:
        held = await _trade(db, state="fully_filled")
        entry = await _order(db, held, state="filled", filled=10)
        db.add(LegPosition(order_id=entry.id, open_qty=Decimal(10)))
        ticket = await _trade(db)
        close = await _order(db, ticket, role="closing", closes=entry.id)
        await recompute_reservation(db, entry_order_id=entry.id)
        await db.flush()
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == entry.id))).scalar_one()
        assert float(lp.reserved_qty) == 10

        assert await end_order(db, close, "rejected", reason="Error 201: margin", now=NOW) is True
        await db.flush()
        assert (close.state, close.rejection_text) == ("rejected", "Error 201: margin")
        assert float(lp.reserved_qty) == 0, "the leg is free again"
        assert (await db.get(TradeStructure, ticket.id)).state == "fully_failed", "the trade is final"
        assert (await db.get(TradeStructure, held.id)).state == "fully_filled", "the trade it was closing is untouched"
        # idempotent, and a filled order never comes through here
        assert await end_order(db, close, "cancelled", now=NOW) is False
        assert await end_order(db, entry, "expired", now=NOW) is False and entry.state == "filled"
        with pytest.raises(ValueError):
            await end_order(db, close, "filled", now=NOW)


async def test_a_trade_waits_for_all_its_entries_and_ignores_a_hedge_that_ended(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s = await _trade(db)
        a = await _order(db, s, idx=0)
        b = await _order(db, s, idx=1, state="filled", filled=10)
        await end_order(db, a, "cancelled", now=NOW)
        assert (await db.get(TradeStructure, s.id)).state == "partial_fail", "one filled, one cancelled"

        done = await _trade(db, state="fully_filled")
        await _order(db, done, state="filled", filled=10)
        hedge = await _order(db, done, role="hedge", idx=100)
        await end_order(db, hedge, "rejected", reason="hedge not placed", now=NOW)
        assert (await db.get(TradeStructure, done.id)).state == "fully_filled", "a hedge does not undo the trade"
        assert await finalise_structure_if_done(db, done.id, now=NOW) is None
        assert b.state == "filled"


def test_no_other_code_ends_an_order_by_hand() -> None:
    """New code must go through ``end_order``. The files below still write an
    ended state themselves; the list may only shrink."""
    still_by_hand = {
        "src/api/routers/positions.py",          # close dispatch failures
        "src/api/orchestration/position_close.py",
        "src/engines/execution/live_submit.py",
        "src/engines/execution/rollback_runner.py",
        "src/engines/execution/package_hedge.py",  # a hedge that could not be placed
        "src/engines/execution/fills_handler.py",  # the combo path
        "src/persistence/order_state.py",
        # not orders: a preview being cancelled, a booked position expiring
        "src/api/routers/trade.py",
        "src/persistence/book_close.py",
    }
    pattern = re.compile(r"""\.state\s*=\s*["'](cancelled|rejected|expired)["']""")
    offenders = set()
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if "migrations" in rel or "spot_orders" in rel or "hedge_executor" in rel:
            continue   # other tables (spot_order, hedge_order) and data migrations
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.add(rel)
    assert offenders <= still_by_hand, f"new direct writers: {sorted(offenders - still_by_hand)}"


# ── #490: nothing is left half-done ──────────────────────────────────────────

class _Executor:
    def __init__(self, trades: list[dict] | None = None) -> None:
        self._trades = trades or []

    def is_connected(self) -> bool:
        return True

    def account_is_reporting(self) -> bool:
        return True

    async def list_all_trades(self) -> list[dict]:
        return list(self._trades)

    async def replay_executions(self) -> list[Any]:
        return []


async def test_a_row_that_never_left_is_ended_and_frees_its_leg(sqlite_sessionmaker: Any) -> None:
    """The api writes its rows, then calls the engine. When that call failed
    the order stayed `pending` for ever: no loop read pending rows."""
    async with sqlite_sessionmaker() as db:
        held = await _trade(db, state="fully_filled")
        entry = await _order(db, held, state="filled", filled=10)
        db.add(LegPosition(order_id=entry.id, open_qty=Decimal(10)))
        ticket = await _trade(db)
        old = await _order(db, ticket, role="closing", closes=entry.id, state="pending", ib_id=None, age_s=900)
        fresh_ticket = await _trade(db)
        fresh = await _order(db, fresh_ticket, role="closing", closes=entry.id, state="pending", ib_id=None, age_s=5, idx=1)
        await recompute_reservation(db, entry_order_id=entry.id)
        await db.commit()
        old_id, fresh_id, entry_id, ticket_id = int(old.id), int(fresh.id), int(entry.id), int(ticket.id)

    out = await reap_stale_orders(sqlite_sessionmaker, _Executor([]), now=NOW)
    assert out["expired"] == [old_id]
    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, old_id)
        assert (row.state, row.rejection_text) == ("expired", "never placed at the broker")
        assert (await db.get(StructureOrder, fresh_id)).state == "pending", "a placement in progress is left alone"
        assert (await db.get(TradeStructure, ticket_id)).state == "fully_failed"
        lp = (await db.execute(select(LegPosition).where(LegPosition.order_id == entry_id))).scalar_one()
        assert float(lp.reserved_qty) == 10, "only the fresh close still reserves"
        ev = (await db.execute(select(TradeEvent).where(TradeEvent.event_type == "order_never_placed"))).scalars().all()
        assert len(ev) == 1


async def test_a_pending_row_whose_order_is_at_ib_is_adopted_not_ended(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s = await _trade(db)
        o = await _order(db, s, state="pending", ib_id=None, age_s=900)
        await db.commit()
        sid, oid = int(s.id), int(o.id)
    live = [{"order_id": 55, "perm_id": 5500, "status": "Submitted", "remaining": 10.0, "order_ref": f"fxvol:{sid}:{oid}"}]
    out = await reap_stale_orders(sqlite_sessionmaker, _Executor(live), now=NOW)
    assert out["expired"] == [] and out["adopted"] == [oid]
    async with sqlite_sessionmaker() as db:
        row = await db.get(StructureOrder, oid)
        assert (row.state, row.ib_order_id) == ("submitted", "55")


async def test_a_filled_trade_left_uncompleted_by_a_restart_is_completed(sqlite_sessionmaker: Any) -> None:
    async with sqlite_sessionmaker() as db:
        s = await _trade(db)                       # still "submitted"
        o = await _order(db, s, state="filled", filled=10)
        db.add(StructureFill(order_id=o.id, ib_execution_id="x1", timestamp=NOW, qty_filled=10, fill_price=0.01,
                             commission_usd=0.0, side="BUY"))
        await db.commit()
        sid = int(s.id)
    out = await reconcile_stuck_orders(sqlite_sessionmaker, _Executor())
    assert out["completed"] == [sid]
    async with sqlite_sessionmaker() as db:
        assert (await db.get(TradeStructure, sid)).state == "fully_filled"
        assert (await db.execute(select(BookedPosition).where(BookedPosition.structure_id == sid))).scalar_one().state == "open"


def test_a_partial_placement_pulls_back_the_legs_still_working() -> None:
    src = (ROOT / "src/engines/execution/live_submit.py").read_text(encoding="utf-8")
    block = src.split("except Exception as e:")[-1]
    assert "executor.cancel_order(" in block and "cancel_requested_order_ids" in block
    assert 'event_type="live_submit_partial_failure", severity="critical"' in block


# ── #491: one door ───────────────────────────────────────────────────────────

async def test_the_manual_ticket_cannot_open_a_position(sqlite_sessionmaker: Any) -> None:
    fop = {"symbol": "EUU", "sec_type": "FOP", "expiry": "20270205", "strike": 1.11, "right": "C", "limit_price": 0.01}
    with pytest.raises(ValueError, match="can only reduce a position"):
        await book_manual_ticket(sqlite_sessionmaker, side="BUY", qty=10, reduce_only=True, **fop)
    async with sqlite_sessionmaker() as db:
        assert (await db.execute(select(StructureOrder))).scalars().all() == [], "nothing is written"


def test_every_engine_route_that_places_an_order_is_known_and_guarded() -> None:
    """The door is a short list. A new route that places an order must be
    added here with its kill switch and ready gate, and say which pre-trade
    check covers it."""
    src = (ROOT / "src/engines/execution/main.py").read_text(encoding="utf-8")
    guarded = set(re.findall(
        r'@router\.post\("([^"]+)", dependencies=\[Depends\(require_not_halted\), Depends\(require_ready\)\]\)', src,
    ))
    assert guarded == {
        "/orders",                       # spot: api gate (run_spot_order_gate); future/option: reduce only
        "/positions/{con_id}/close",     # no longer reachable from the api (410)
        "/positions/close-by-symbol",    # a close of a booked position, guarded by the api
        "/structure/submit",             # the order ticket: full pre-trade gate in the api
        "/structure/rollback",
        "/hedge",                        # monitor hedges, dormant
    }
    assert "reduce_only=True" in src
    api_orders = (ROOT / "src/api/routers/orders.py").read_text(encoding="utf-8")
    assert "run_spot_order_gate(" in api_orders and "raise HTTPException(410" in api_orders
    assert "run_pretrade_gate(" in (ROOT / "src/api/routers/trade.py").read_text(encoding="utf-8")


def test_a_close_keeps_the_operator_limit_and_records_the_price_sent() -> None:
    """#492: the engine replaced any close limit, including one the operator
    typed, with the live quote, and the row kept the api's guess."""
    executor = (ROOT / "src/engines/execution/order_executor.py").read_text(encoding="utf-8")
    assert "limit_price if (limit_is_operator and limit_price) else await _marketable_close_price(" in executor
    api = (ROOT / "src/api/routers/positions.py").read_text(encoding="utf-8")
    assert 'payload["limit_is_operator"] = limit_price_override is not None' in api
    assert 'closing_order.limit_price = float(sent_price)' in api

