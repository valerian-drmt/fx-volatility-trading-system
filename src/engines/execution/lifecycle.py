"""Contract lifecycle: an expired contract closes its book legs (#463).

When a contract expires IB drops the position without an execution, so the
fill-driven book never learnt it. Every leg on a contract expired between
August and November 2026 was still "open" in ``leg_position`` in November: 24
permanent ``missing_at_ib`` breaks on prod.

One pass, at start and every ``LIFECYCLE_INTERVAL_S``:

1. every leg with a non-zero book quantity whose contract is past its expiry
   AND that the broker no longer holds gets an ``expiry`` position event that
   brings it to zero;
2. every trade whose legs are all flat is closed (the safety net behind the
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
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.contracts import INSTRUMENT_SPOT, contract_expiry, is_linear, parse_local_symbol
from persistence.book_close import REASON_EXPIRY, close_structure_if_flat
from persistence.instruments import expiries_by_symbol
from persistence.models import LegPosition, OpenPosition, StructureOrder, TradeStructure
from persistence.position_events import record_expiry

logger = logging.getLogger(__name__)

LIFECYCLE_INTERVAL_S = 3600.0
_CLOSABLE = ("partial_fill", "fully_filled", "partial_fail")


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
        out["closed_flat"] = await close_flat_trades(db, now=now)
        await db.commit()
    if out["expired_legs"] or out["closed_flat"] or out["closed_trades"]:
        logger.info(
            "lifecycle_pass expired_legs=%s closed_expired=%s closed_flat=%s",
            out["expired_legs"], out["closed_trades"], out["closed_flat"],
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
