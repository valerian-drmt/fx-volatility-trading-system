"""Contract lifecycle: an expired contract closes its book legs (#463).

When a contract expires IB drops the position without an execution, so the
fill-driven book never learnt it. Every leg on a contract expired between
August and November 2026 was still "open" in ``leg_position`` in November: 24
permanent ``missing_at_ib`` breaks on prod.

One pass, at start and every ``LIFECYCLE_INTERVAL_S``:

1. every leg with a non-zero book quantity whose contract is past its expiry
   AND that the broker no longer holds gets an ``expiry`` position event that
   brings it to zero;
2. every leg on a contract the broker has not held for a day is written off
   (``write_off_unheld``): the book is corrected to what the broker holds;
3. every trade whose legs are all flat is closed (the safety net behind the
   fill handler's own check).

Guards: nothing runs unless IB is reporting the account (same rule as the
reaper and the reconciler), and a contract the broker still lists is never
expired by us; that case is logged for the operator.

Price of the event: 0 for an option (expired worthless, or exercised: see
below), the leg's own average for a future (no P&L invented; the settlement
price is not known here). Exercise and assignment are not derived yet: IB
reports them as executions on the option and on the future, which the fill
path books when they belong to a known order.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.contracts import INSTRUMENT_SPOT, contract_expiry, is_linear, parse_local_symbol
from persistence.book_close import REASON_EXPIRY, REASON_WRITEOFF, close_structure_if_flat
from persistence.instruments import expiries_by_symbol
from persistence.models import LegPosition, OpenPosition, StructureOrder, TradeEvent, TradeStructure
from persistence.position_events import record_expiry, record_writeoff

logger = logging.getLogger(__name__)

LIFECYCLE_INTERVAL_S = 3600.0
# A leg on a contract the broker has not held, with no execution on that
# contract for this long, is an error in the book and is written off.
WRITEOFF_AFTER = timedelta(hours=24)
_CLOSABLE = ("fully_filled", "partial_fail")


async def expire_legs(db: AsyncSession, *, today: date, now: datetime) -> dict[str, Any]:
    """Write the expiry events due today. Caller commits."""
    held = set((await db.execute(select(OpenPosition.structure))).scalars().all())
    rows = (await db.execute(
        select(LegPosition, StructureOrder)
        .join(StructureOrder, StructureOrder.id == LegPosition.order_id)
        .where(LegPosition.open_qty != 0)
        .where(StructureOrder.ib_local_symbol.is_not(None))
    )).all()
    listed = await expiries_by_symbol(db, sorted({str(o.ib_local_symbol) for _, o in rows}))
    expired: list[int] = []
    still_held: list[str] = []
    structures: set[int] = set()
    for lp, order in rows:
        sym = str(order.ib_local_symbol)
        spec = parse_local_symbol(sym)
        if spec is not None and spec.instrument_type == INSTRUMENT_SPOT:
            continue   # spot never expires
        expiry = contract_expiry(sym, listed.get(sym), today)
        if expiry is None or expiry >= today:
            continue
        if sym in held:
            still_held.append(sym)
            continue
        linear = spec is not None and is_linear(spec.instrument_type)
        price = float(lp.avg_price or 0.0) if linear else 0.0
        note = (
            "future past its last trading day and no longer held; settlement price unknown, closed at cost"
            if linear else "option past its expiry and no longer held"
        )
        ev = await record_expiry(db, order=order, expiry=expiry, price=price, note=note, now=now)
        if ev is not None:
            expired.append(int(order.id))
            structures.add(int(order.structure_id))
    closed: list[int] = []
    for sid in sorted(structures):
        if await close_structure_if_flat(db, structure_id=sid, reason=REASON_EXPIRY, now=now):
            closed.append(sid)
    if still_held:
        logger.warning("lifecycle_expired_but_still_held contracts=%s", sorted(set(still_held)))
    return {"expired_legs": expired, "closed_trades": closed, "expired_but_held": sorted(set(still_held))}


async def write_off_unheld(db: AsyncSession, *, now: datetime) -> dict[str, Any]:
    """The cross-check that corrects the book: what the broker does not hold,
    the book must not hold either.

    A contract with book quantity, absent from the broker mirror, and with no
    execution of ours for ``WRITEOFF_AFTER``, is a wrong book (a fill that
    never reached us, a position closed outside the system, test residue).
    Every leg on it gets a ``writeoff`` event that brings it to zero, at its
    own average price (no P&L invented), and the trades left flat are closed.
    Each write-off is a warning event: the operator sees what was corrected.

    A fresh fill is in the mirror within seconds, so "not held for a day"
    cannot be a position we really have. Spot is cash and is never touched.
    Caller commits; the caller has checked that IB is reporting."""
    held = set((await db.execute(select(OpenPosition.structure))).scalars().all())
    rows = (await db.execute(
        select(LegPosition, StructureOrder)
        .join(StructureOrder, StructureOrder.id == LegPosition.order_id)
        .where(LegPosition.open_qty != 0)
        .where(StructureOrder.ib_local_symbol.is_not(None))
    )).all()
    by_contract: dict[str, list[tuple[LegPosition, StructureOrder]]] = {}
    for lp, order in rows:
        by_contract.setdefault(str(order.ib_local_symbol), []).append((lp, order))
    # a close still working on the contract means it is being dealt with
    working = set((await db.execute(
        select(StructureOrder.ib_local_symbol)
        .where(StructureOrder.state.in_(("pending", "submitted", "partially_filled")))
        .where(StructureOrder.ib_local_symbol.is_not(None))
    )).scalars().all())
    cutoff = now - WRITEOFF_AFTER
    written: dict[str, float] = {}
    structures: set[int] = set()
    for contract, legs in sorted(by_contract.items()):
        spec = parse_local_symbol(contract)
        if contract in held or contract in working:
            continue
        if spec is not None and spec.instrument_type == INSTRUMENT_SPOT:
            continue
        last = max(
            (_aware(o.fully_filled_at or o.state_updated_at or o.submitted_at) for _, o in legs),
            default=None,
        )
        if last is not None and last > cutoff:
            continue   # traded recently: give the mirror and the fills time to agree
        net = sum(float(lp.open_qty or 0) for lp, _ in legs)
        for _lp, order in legs:
            ev = await record_writeoff(db, order=order, now=now)
            if ev is not None:
                structures.add(int(order.structure_id))
        written[contract] = net
        db.add(TradeEvent(
            structure_id=None, event_type="position_written_off", severity="warning",
            description=f"book held {net:+g} {contract}, the broker holds none: written off"[:300],
            payload={"contract": contract, "book_net_qty": net, "legs": [int(o.id) for _, o in legs]},
        ))
        logger.warning("lifecycle_written_off contract=%s book_net_qty=%s legs=%s", contract, net, len(legs))
    closed: list[int] = []
    for sid in sorted(structures):
        if await close_structure_if_flat(db, structure_id=sid, reason=REASON_WRITEOFF, now=now):
            closed.append(sid)
    return {"written_off": written, "closed_written_off": closed}


def _aware(ts: datetime | None) -> datetime:
    if ts is None:
        return datetime.min.replace(tzinfo=UTC)
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


async def close_flat_trades(db: AsyncSession, *, now: datetime) -> list[int]:
    """Close every trade that has traded and holds nothing. Caller commits."""
    ids = (await db.execute(
        select(TradeStructure.id).where(TradeStructure.state.in_(_CLOSABLE))
    )).scalars().all()
    closed: list[int] = []
    for sid in ids:
        if await close_structure_if_flat(db, structure_id=int(sid), now=now):
            closed.append(int(sid))
    return closed


async def lifecycle_pass(
    sm: async_sessionmaker[AsyncSession], executor: Any, *, now: datetime | None = None,
) -> dict[str, Any]:
    if not executor.account_is_reporting():
        return {"skipped": "account_not_reporting"}
    now = now or datetime.now(UTC)
    async with sm() as db:
        out = await expire_legs(db, today=now.date(), now=now)
        await db.flush()
        out.update(await write_off_unheld(db, now=now))
        out["closed_flat"] = await close_flat_trades(db, now=now)
        await db.commit()
    if out["expired_legs"] or out["closed_flat"] or out["closed_trades"] or out["written_off"]:
        logger.info(
            "lifecycle_pass expired_legs=%s closed_expired=%s written_off=%s closed_flat=%s",
            out["expired_legs"], out["closed_trades"], out["written_off"], out["closed_flat"],
        )
    return out


async def lifecycle_loop(
    sm: async_sessionmaker[AsyncSession], executor: Any, *, interval_s: float = LIFECYCLE_INTERVAL_S,
) -> None:
    """Run forever. The first pass waits for IB to report, then one an hour."""
    while True:
        try:
            out = await lifecycle_pass(sm, executor)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("lifecycle_loop_error")
            out = {}
        await asyncio.sleep(60.0 if out.get("skipped") else interval_s)
