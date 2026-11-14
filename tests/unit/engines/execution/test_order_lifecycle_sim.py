"""Order-lifecycle scenarios against a simulated broker (#496).

Each test drives the REAL engine code (live submit, the fill and status
handlers, the reaper, the reconciler, the order-state module) against
``tests/fixtures/sim_broker.SimBroker`` and ends on one statement: the book
equals the broker. The scenarios are those of the audit of 2026-11-13
(``docs/audits/reports/order-lifecycle/05-how-it-can-collapse.md``, S2 to
S8), which were derived from reading the code and had never been run.

No IB, no network: the suite runs in the unit job.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select

from engines.execution import fills_handler
from engines.execution.fills_handler import attach_fill_handlers
from engines.execution.live_submit import LiveSubmitError, submit_structure_live
from engines.execution.live_submit import order_ref as structure_order_ref
from engines.execution.order_executor import OrderExecutor
from engines.execution.order_reconciler import reconcile_stuck_orders
from engines.execution.placement import claim_submit, stamp_placement
from engines.execution.reaper import reap_stale_orders
from persistence.models import LegPosition, StructureFill, StructureOrder, TradeEvent, TradeStructure
from persistence.reservation import recompute_reservation
from tests.fixtures.sim_broker import SimBroker

pytestmark = pytest.mark.unit

TERMINAL = ("filled", "cancelled", "rejected", "expired")
LATER = timedelta(minutes=10)      # past the reaper's 5 minutes


# ── harness ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _one_engine_per_test() -> Any:
    """Each test is one engine process: its per-order locks start empty (they
    are bound to the event loop that created them)."""
    fills_handler._ORDER_LOCKS.clear()
    yield
    fills_handler._ORDER_LOCKS.clear()


def _engine() -> tuple[OrderExecutor, SimBroker]:
    """The real executor, with the simulated broker where ib_async.IB stands."""
    broker = SimBroker()
    ex = OrderExecutor("sim", 0, 0)
    ex._ib = broker
    return ex, broker


async def _settle() -> None:
    """Let every handler the broker's events started run to its end."""
    for _ in range(20):
        pending = [t for t in fills_handler._BG if not t.done()]
        if not pending:
            await asyncio.sleep(0)
            if not [t for t in fills_handler._BG if not t.done()]:
                return
            continue
        await asyncio.gather(*pending, return_exceptions=True)
    raise AssertionError("handlers still running")


async def _seed(sm: Any, *, legs: int = 1, qty: int = 10) -> tuple[int, list[int]]:
    async with sm() as db:
        s = TradeStructure(structure_type="long call", reference_tenor="3M", base_qty=qty,
                           state="submitted", execution_mode="live")
        db.add(s)
        await db.flush()
        ids = []
        for i in range(legs):
            o = StructureOrder(
                structure_id=s.id, leg_idx=i, order_role="entry", contract_type="call",
                contract_strike=1.11 + i * 0.01, contract_expiry=date(2027, 2, 5), side="BUY", qty=qty,
                order_type="LMT", limit_price=0.0042, preview_price=0.0042, state="pending",
            )
            db.add(o)
            await db.flush()
            ids.append(int(o.id))
        await db.commit()
        return int(s.id), ids


async def _row(sm: Any, order_id: int) -> StructureOrder:
    async with sm() as db:
        return await db.get(StructureOrder, order_id)


async def _struct(sm: Any, sid: int) -> TradeStructure:
    async with sm() as db:
        return await db.get(TradeStructure, sid)


async def _events(sm: Any) -> set[str]:
    async with sm() as db:
        return set((await db.execute(select(TradeEvent.event_type))).scalars().all())


async def _ib_id(sm: Any, order_id: int) -> int:
    return int((await _row(sm, order_id)).ib_order_id)


async def _close(sm: Any, ex: OrderExecutor, entry_id: int, qty: int) -> tuple[int, int]:
    """A close as the api and the engine make one: the api writes the ticket
    and reserves the leg, the engine claims the row, places and wires it."""
    async with sm() as db:
        entry = await db.get(StructureOrder, entry_id)
        ticket = TradeStructure(structure_type="close", reference_tenor="3M", base_qty=qty,
                                state="submitted", execution_mode="live")
        db.add(ticket)
        await db.flush()
        close = StructureOrder(
            structure_id=ticket.id, leg_idx=0, order_role="closing", contract_type=entry.contract_type,
            contract_strike=entry.contract_strike, contract_expiry=entry.contract_expiry,
            side="SELL", qty=qty, order_type="LMT", limit_price=0.0041, state="pending",
            closes_order_id=entry_id, ib_local_symbol=entry.ib_local_symbol,
        )
        db.add(close)
        await db.flush()
        await recompute_reservation(db, entry_order_id=entry_id)
        await db.commit()
        close_id, ticket_id, symbol = int(close.id), int(ticket.id), str(entry.ib_local_symbol)
    assert await claim_submit(sm, StructureOrder, close_id)
    trade = await ex.close_position_by_symbol(
        local_symbol=symbol, qty=qty, limit_price=0.0041,
        order_ref=structure_order_ref(ticket_id, close_id), limit_is_operator=True,
    )
    await stamp_placement(sm, StructureOrder, close_id, trade)
    attach_fill_handlers(trade=trade, order_id=close_id, sessionmaker_factory=sm)
    return close_id, ticket_id


async def _book(sm: Any) -> dict[str, int]:
    """Position per contract as the book recorded it: the sum of its fills."""
    async with sm() as db:
        rows = (await db.execute(
            select(StructureOrder.ib_local_symbol, StructureOrder.side, StructureFill.qty_filled)
            .join(StructureFill, StructureFill.order_id == StructureOrder.id)
        )).all()
    out: dict[str, int] = {}
    for symbol, side, qty in rows:
        out[symbol] = out.get(symbol, 0) + (1 if side == "BUY" else -1) * int(qty)
    return {k: v for k, v in out.items() if v}


async def _assert_book_equals_broker(sm: Any, broker: SimBroker) -> None:
    """The statement every scenario ends on.

    1. Positions: per contract, the fills the book recorded add up to the
       executions the broker made.
    2. Orders: a row has ended exactly when its order has ended at the
       broker; a row that says "working" has an order working there.
    3. Quantities: each row's filled quantity is the broker's.
    """
    await _settle()
    broker_positions = {
        s: broker.position(s) for s in {f.contract.localSymbol for f in broker.executions} if broker.position(s)
    }
    assert await _book(sm) == broker_positions, "positions differ"
    async with sm() as db:
        rows = (await db.execute(select(StructureOrder))).scalars().all()
    at_broker = set(broker.ids())
    for o in rows:
        if o.ib_order_id is None:
            assert o.state in ("pending", *TERMINAL)
            continue
        oid = int(o.ib_order_id)
        assert oid in at_broker, f"order {o.id} carries an id the broker does not know"
        assert (o.state in TERMINAL) == (not broker.is_working(oid)), (
            f"order {o.id} is {o.state} in the book and {broker.status(oid)} at the broker")
        assert int(o.qty_filled or 0) == int(broker._find(oid).orderStatus.filled), f"order {o.id}: filled quantity"
    placed_refs = {(t.order.orderRef or "") for t in broker._trades}
    for o in rows:
        if o.ib_order_id is None and o.state == "pending":
            assert structure_order_ref(int(o.structure_id), int(o.id)) not in placed_refs, (
                f"order {o.id} is at the broker and the book does not know")


# ── the plain path, as a reference ──────────────────────────────────────────

async def test_an_order_is_placed_acknowledged_partly_then_fully_filled(sqlite_sessionmaker: Any) -> None:
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (oid,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    ib_id = await _ib_id(sm, oid)
    assert (await _row(sm, oid)).state == "submitted"

    broker.ack(ib_id)
    broker.fill(ib_id, 4)
    await _settle()
    row = await _row(sm, oid)
    assert (row.state, row.qty_filled) == ("partially_filled", 4)
    await _assert_book_equals_broker(sm, broker)

    broker.fill(ib_id)                  # the six that remain
    await _settle()
    assert (await _row(sm, oid)).state == "filled"
    assert (await _struct(sm, sid)).state == "fully_filled"
    await _assert_book_equals_broker(sm, broker)
    assert await _book(sm) == {"EUUG7 C1110": 10}


# ── S2: a multi-leg order whose second leg is refused ───────────────────────

async def test_s2_second_leg_refused_the_first_is_pulled_back(sqlite_sessionmaker: Any) -> None:
    """The first leg is live when the second cannot be placed. It must not be
    left out alone: the engine asks for its cancel and says so."""
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (first, second) = await _seed(sm, legs=2)
    broker.fail_place_number = 2
    with pytest.raises(LiveSubmitError):
        await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    ib_first = await _ib_id(sm, first)
    assert broker.cancel_requests == [ib_first], "the leg still working is pulled back"
    assert (await _row(sm, first)).cancel_requested_at is not None
    assert (await _row(sm, second)).ib_order_id is None
    assert "live_submit_partial_failure" in await _events(sm)

    broker.confirm_cancel(ib_first)
    await _settle()
    assert (await _row(sm, first)).state == "cancelled"
    assert (await _struct(sm, sid)).state == "partial_fail"
    await _assert_book_equals_broker(sm, broker)
    assert await _book(sm) == {}, "nothing is held"


async def test_s2_the_first_leg_fills_before_its_cancel_is_confirmed(sqlite_sessionmaker: Any) -> None:
    """A cancel is a request: the leg can fill first. The fill is a real
    position and the book records it; nothing is unwound automatically."""
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (first, _second) = await _seed(sm, legs=2)
    broker.fail_place_number = 2
    with pytest.raises(LiveSubmitError):
        await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    ib_first = await _ib_id(sm, first)
    broker.fill(ib_first)                       # filled while the cancel travelled
    await _settle()
    assert (await _row(sm, first)).state == "filled"
    await _assert_book_equals_broker(sm, broker)
    assert await _book(sm) == {"EUUG7 C1110": 10}
    assert len(broker.ids()) == 1, "no order was sent to undo the leg"


# ── S3: a partial fill is not a fill ────────────────────────────────────────

async def test_s3_a_partly_filled_order_is_not_marked_filled_by_the_reconciler(sqlite_sessionmaker: Any) -> None:
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (oid,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    ib_id = await _ib_id(sm, oid)
    broker.ack(ib_id)
    broker.fill(ib_id, 3)
    await _settle()
    for _ in range(3):                          # the reconciler runs every minute
        await reconcile_stuck_orders(sm, ex)
    row = await _row(sm, oid)
    assert (row.state, row.qty_filled) == ("partially_filled", 3)
    assert (await _struct(sm, sid)).state == "submitted"
    await _assert_book_equals_broker(sm, broker)


# ── S4: the engine dies between the send and the commit ─────────────────────

async def test_s4_an_order_placed_and_not_recorded_is_adopted_then_followed(sqlite_sessionmaker: Any) -> None:
    """Fault injected between two writes of a placement: the order is at the
    broker, the row still says pending with no broker id. A retry must not
    place it twice, the reaper must not end it, and its fills must be booked."""
    from ib_async import LimitOrder

    from tests.fixtures.sim_broker import option

    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (oid,) = await _seed(sm, qty=10)
    order = LimitOrder("BUY", 10, 0.0042)
    order.orderRef = structure_order_ref(sid, oid)
    order.tif = "DAY"
    ghost = broker.placeOrder(option(), order)          # sent; the engine stops here
    ib_id = int(ghost.order.orderId)

    summary = await reap_stale_orders(sm, ex, now=datetime.now(UTC) + LATER)
    assert summary["adopted"] == [oid] and summary["expired"] == []
    row = await _row(sm, oid)
    assert (row.state, row.ib_order_id) == ("submitted", str(ib_id))
    assert len(broker.ids()) == 1, "adopted, not placed a second time"

    broker.fill(ib_id, silent=True)             # no handler was ever attached to it
    await reconcile_stuck_orders(sm, ex)
    await _settle()
    assert (await _row(sm, oid)).state == "filled"
    await _assert_book_equals_broker(sm, broker)


async def test_s4_a_row_that_never_left_is_ended_and_frees_its_leg(sqlite_sessionmaker: Any) -> None:
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (entry,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    broker.fill(await _ib_id(sm, entry))
    await _settle()
    # the api writes a close and reserves the leg; the engine is unreachable
    async with sm() as db:
        e = await db.get(StructureOrder, entry)
        ticket = TradeStructure(structure_type="close", reference_tenor="3M", base_qty=10,
                                state="submitted", execution_mode="live")
        db.add(ticket)
        await db.flush()
        old = datetime.now(UTC) - LATER
        close = StructureOrder(
            structure_id=ticket.id, leg_idx=0, order_role="closing", contract_type="call", side="SELL",
            qty=10, order_type="LMT", limit_price=0.0041, state="pending", closes_order_id=entry,
            ib_local_symbol=e.ib_local_symbol, state_updated_at=old,
        )
        db.add(close)
        await db.flush()
        await recompute_reservation(db, entry_order_id=entry)
        await db.commit()
        close_id, ticket_id = int(close.id), int(ticket.id)
        assert (await db.get(LegPosition, entry)).reserved_qty == Decimal(10)

    summary = await reap_stale_orders(sm, ex)
    assert close_id in summary["expired"]
    row = await _row(sm, close_id)
    assert (row.state, row.rejection_text) == ("expired", "never placed at the broker")
    async with sm() as db:
        assert (await db.get(LegPosition, entry)).reserved_qty == Decimal(0), "the leg can be closed again"
    assert (await _struct(sm, ticket_id)).state == "fully_failed"
    await _assert_book_equals_broker(sm, broker)


# ── S5: a close that rests counts for as long as it rests ───────────────────

async def test_s5_a_resting_close_keeps_its_leg_reserved_whatever_its_age(sqlite_sessionmaker: Any) -> None:
    """On 2026-11-12 a second SELL 100 was accepted on a long of 100: the
    first close stopped counting after 3 minutes."""
    from api.routers.positions import _INFLIGHT_CLOSE_WINDOW, close_inflight_remaining

    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (entry,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    broker.fill(await _ib_id(sm, entry))
    await _settle()
    close_id, _ticket = await _close(sm, ex, entry, 10)
    broker.ack(await _ib_id(sm, close_id))
    await _settle()

    hours_later = datetime.now(UTC) + timedelta(hours=6)
    row = await _row(sm, close_id)
    assert row.state == "submitted"
    assert close_inflight_remaining(
        row.qty, row.qty_filled, row.submitted_at, row.state_updated_at,
        hours_later, _INFLIGHT_CLOSE_WINDOW, row.state,
    ) == 10, "the whole position is still being closed"
    async with sm() as db:
        assert (await db.get(LegPosition, entry)).reserved_qty == Decimal(10)
    await _assert_book_equals_broker(sm, broker)


# ── S6: an order the broker holds ───────────────────────────────────────────

async def test_s6_an_order_ib_holds_is_not_expired_and_its_late_fill_is_booked(sqlite_sessionmaker: Any) -> None:
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (oid,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    ib_id = await _ib_id(sm, oid)
    broker.hold(ib_id)                          # Inactive, no error: outside trading hours
    await _settle()
    assert "order_held" in await _events(sm)

    summary = await reap_stale_orders(sm, ex, now=datetime.now(UTC) + LATER)
    assert summary["expired"] == []
    assert (await _row(sm, oid)).state == "submitted"
    await _assert_book_equals_broker(sm, broker)

    broker.ack(ib_id)                           # the market opens
    broker.fill(ib_id)
    await _settle()
    assert (await _row(sm, oid)).state == "filled"
    await _assert_book_equals_broker(sm, broker)


# ── S7: the broker refuses or ends a close ──────────────────────────────────

async def test_s7_a_close_the_broker_rejects_frees_the_leg_and_ends_its_ticket(sqlite_sessionmaker: Any) -> None:
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (entry,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    broker.fill(await _ib_id(sm, entry))
    await _settle()
    close_id, ticket_id = await _close(sm, ex, entry, 10)
    async with sm() as db:
        assert (await db.get(LegPosition, entry)).reserved_qty == Decimal(10)

    broker.reject(await _ib_id(sm, close_id), "Order rejected - reason: price outside limits")
    await _settle()
    row = await _row(sm, close_id)
    assert row.state == "rejected" and "price outside limits" in row.rejection_text
    async with sm() as db:
        assert (await db.get(LegPosition, entry)).reserved_qty == Decimal(0)
    assert (await _struct(sm, ticket_id)).state == "fully_failed"
    assert (await _struct(sm, sid)).state == "fully_filled", "the trade it was closing is untouched"
    await _assert_book_equals_broker(sm, broker)
    assert await _book(sm) == {"EUUG7 C1110": 10}


# ── S8: a restart right after the last fill ─────────────────────────────────

async def test_s8_a_fill_the_engine_never_saw_is_booked_after_a_restart(sqlite_sessionmaker: Any) -> None:
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (first, second) = await _seed(sm, legs=2)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    broker.fill(await _ib_id(sm, first))
    await _settle()
    broker.fill(await _ib_id(sm, second), silent=True)      # the engine is down when it happens
    broker.restart_engine_view()                             # new session: no handler, done orders unlisted
    assert (await _row(sm, second)).state == "submitted"
    assert (await _struct(sm, sid)).state == "submitted"

    await reconcile_stuck_orders(sm, ex)                    # asks the broker for the day's executions
    await _settle()
    assert (await _row(sm, second)).state == "filled"
    assert (await _struct(sm, sid)).state == "fully_filled", "the trade is completed, not left half-done"
    await _assert_book_equals_broker(sm, broker)


# ── the end of the day, and the desk's own cancel ───────────────────────────

@pytest.mark.parametrize("heard", [True, False], ids=["handler", "reaper"])
async def test_a_day_order_that_ends_unfilled_reads_expired_whoever_sees_it(
    sqlite_sessionmaker: Any, heard: bool,
) -> None:
    """#485: the status handler wrote "cancelled", the reaper "expired"."""
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (oid,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    broker.ack(await _ib_id(sm, oid))
    await _settle()

    broker.end_of_day(silent=not heard)
    await _settle()
    if not heard:
        assert (await _row(sm, oid)).state == "submitted"
        await reap_stale_orders(sm, ex, now=datetime.now(UTC) + LATER)
    row = await _row(sm, oid)
    assert row.state == "expired" and row.cancel_requested_at is None
    assert (await _struct(sm, sid)).state == "fully_failed"
    await _assert_book_equals_broker(sm, broker)


async def test_a_cancel_by_the_desk_is_true_when_the_broker_confirms_it(sqlite_sessionmaker: Any) -> None:
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (oid,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    ib_id = await _ib_id(sm, oid)
    broker.ack(ib_id)
    broker.fill(ib_id, 4)
    await _settle()

    assert await ex.cancel_order(ib_id) is not None
    async with sm() as db:                      # what the api records with the request
        (await db.get(StructureOrder, oid)).cancel_requested_at = datetime.now(UTC)
        await db.commit()
    assert (await _row(sm, oid)).state == "partially_filled", "asked is not cancelled"

    broker.fill(ib_id, 2)                       # two more fill before the broker acts on it
    broker.confirm_cancel(ib_id)
    await _settle()
    row = await _row(sm, oid)
    assert (row.state, row.qty_filled) == ("cancelled", 6)
    await _assert_book_equals_broker(sm, broker)
    assert await _book(sm) == {"EUUG7 C1110": 6}


# ── a dead feed proves nothing ──────────────────────────────────────────────

async def test_nothing_is_expired_while_the_broker_cannot_be_asked(sqlite_sessionmaker: Any) -> None:
    sm = sqlite_sessionmaker
    ex, broker = _engine()
    sid, (oid,) = await _seed(sm, qty=10)
    await submit_structure_live(sessionmaker_factory=sm, executor=ex, structure_id=sid)
    ib_id = await _ib_id(sm, oid)
    broker.ack(ib_id)
    await _settle()

    broker.drop_connection()
    summary = await reap_stale_orders(sm, ex, now=datetime.now(UTC) + LATER)
    assert summary["expired"] == [] and "skipped" in summary
    assert (await _row(sm, oid)).state == "submitted"

    broker.restore_connection()
    broker.fill(ib_id)                          # it was working all along
    await _settle()
    assert (await _row(sm, oid)).state == "filled"
    await _assert_book_equals_broker(sm, broker)
