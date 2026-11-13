"""A manual single-contract ticket is booked like any trade (#466).

``POST /orders`` for a future or an option used to place the order with no
row at all: the fill never reached the book, and the reconciler reported the
position as ``unbooked_at_ib`` by construction.

Now the ticket writes a ``trade_structure`` with one ``trade_order`` BEFORE
the order goes to IB, and the usual fill handlers keep it current.

* If the ticket reduces a leg the book holds on that contract (opposite side,
  enough quantity available), it is booked as a **closing** order of that leg
  (``closes_order_id``), so the leg nets and its trade can close.
* Otherwise it is a new **entry**: a one-leg trade of its own.

Spot FX is deliberately NOT booked here. IB reports a spot trade as a virtual
FX position that it stops listing after its nightly restart (prod: the
position of 2026-11-09 left the mirror at 23:59), so a booked spot leg could
never reconcile. Spot stays in ``spot_order``; the position panels show the
broker's spot row as cash, and the reconciler does not raise a break for it.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.contracts import build_ib_local_symbol
from persistence.models import Instrument, LegPosition, StructureOrder, TradeStructure
from persistence.order_state import end_order
from persistence.reservation import recompute_reservation
from shared.trace import current_trace_id

logger = logging.getLogger(__name__)

_SEC_TYPES = ("FUT", "FOP")


def _contract_type(sec_type: str, right: str | None) -> str:
    if sec_type == "FUT":
        return "future"
    return "call" if (right or "").upper() == "C" else "put"


def _structure_type(contract_type: str, side: str, closing: bool) -> str:
    """Same names as the Close panel writes (``positions._structure_type_for_close``):
    for a close, the side is the side of the position being closed."""
    if contract_type == "future":
        position_side = ("SELL" if side == "BUY" else "BUY") if closing else side
        return "future_buy" if position_side == "BUY" else "future_sell"
    return "vanilla_call" if contract_type == "call" else "vanilla_put"


def _expiry(raw: str | None) -> date | None:
    try:
        return datetime.strptime(raw or "", "%Y%m%d").replace(tzinfo=UTC).date()
    except ValueError:
        return None


async def _local_symbol(
    db: AsyncSession, *, sec_type: str, contract_type: str, contract_symbol: str,
    expiry: date | None, strike: float | None, right: str | None,
) -> str | None:
    """The IB local symbol of the ticket's contract: from the instruments the
    broker has shown us when it is one of them, else built from the fields (a
    manual ticket names a listed contract, so the build is exact)."""
    if expiry is not None:
        q = select(Instrument).where(Instrument.sec_type == sec_type, Instrument.expiry == expiry)
        for inst in (await db.execute(q)).scalars().all():
            if sec_type == "FOP":
                if inst.option_right != (right or "").upper() or inst.strike is None or strike is None:
                    continue
                if abs(float(inst.strike) - float(strike)) > 1e-6:
                    continue
            elif (inst.symbol or "EUR") != contract_symbol and not str(inst.local_symbol).startswith(
                "M6E" if contract_symbol == "M6E" else "6E",
            ):
                continue
            return str(inst.local_symbol)
    return build_ib_local_symbol(contract_type, expiry, strike, contract_symbol)


async def _leg_to_close(db: AsyncSession, *, local_symbol: str, side: str, qty: int) -> StructureOrder | None:
    """Oldest entry leg on this contract that the ticket reduces and that has
    the whole quantity available (open minus what closes in flight reserve)."""
    rows = (await db.execute(
        select(StructureOrder, LegPosition)
        .join(LegPosition, LegPosition.order_id == StructureOrder.id)
        .where(StructureOrder.ib_local_symbol == local_symbol)
        .where(StructureOrder.order_role == "entry")
        .where(LegPosition.open_qty != 0)
        .order_by(StructureOrder.id)
    )).all()
    for order, lp in rows:
        open_qty = float(lp.open_qty or 0)
        reduces = (open_qty > 0 and side == "SELL") or (open_qty < 0 and side == "BUY")
        if reduces and abs(open_qty) - float(lp.reserved_qty or 0) >= qty:
            return order
    return None


async def book_manual_ticket(
    sm: async_sessionmaker[AsyncSession], *,
    symbol: str, sec_type: str, side: str, qty: int, limit_price: float | None,
    expiry: str | None, strike: float | None, right: str | None,
    exchange: str = "CME", currency: str = "USD", reduce_only: bool = False,
) -> tuple[int, int]:
    """Write the trade and its order, ``pending``. Returns
    ``(structure_id, order_id)``; the caller places the order and wires the
    fill handlers on ``order_id``."""
    if sec_type not in _SEC_TYPES:
        raise ValueError(f"manual tickets are booked for {_SEC_TYPES}, not {sec_type!r}")
    contract_type = _contract_type(sec_type, right)
    contract_symbol = "M6E" if symbol == "M6E" else "EUR"
    expiry_date = _expiry(expiry)
    now = datetime.now(UTC)
    async with sm() as db:
        local_symbol = await _local_symbol(
            db, sec_type=sec_type, contract_type=contract_type, contract_symbol=contract_symbol,
            expiry=expiry_date, strike=strike, right=right,
        )
        target = (
            await _leg_to_close(db, local_symbol=local_symbol, side=side, qty=int(qty))
            if local_symbol else None
        )
        closing = target is not None
        if reduce_only and not closing:
            # One door (#491): a NEW position is opened through the order
            # ticket, where the pre-trade checks are. This route had none of
            # them: no cap, no symbol list, no price collar.
            raise ValueError(
                "this ticket can only reduce a position the book holds: no leg with "
                f"{qty} available on {local_symbol or 'that contract'}; open through the order ticket"
            )
        struct = TradeStructure(
            structure_type=_structure_type(contract_type, side, closing),
            reference_tenor="manual", expiry_date=expiry_date, base_qty=int(qty),
            state="submitted", state_updated_at=now, execution_mode="live",
            trace_id=current_trace_id(),
        )
        db.add(struct)
        await db.flush()
        order = StructureOrder(
            structure_id=struct.id, leg_idx=0, order_role="closing" if closing else "entry",
            closes_order_id=int(target.id) if target is not None else None,
            contract_symbol=contract_symbol, contract_type=contract_type,
            contract_expiry=expiry_date, contract_strike=strike,
            contract_exchange=exchange[:10], contract_currency=currency[:5],
            side=side, qty=int(qty), order_type="LMT" if limit_price else "MKT",
            limit_price=limit_price, state="pending", state_updated_at=now,
            trace_id=current_trace_id(),
        )
        db.add(order)
        await db.flush()
        if target is not None:
            await recompute_reservation(db, entry_order_id=int(target.id))
        await db.commit()
        logger.info(
            "manual_ticket_booked structure_id=%s order_id=%s role=%s closes=%s contract=%s",
            struct.id, order.id, order.order_role, order.closes_order_id, local_symbol,
        )
        return int(struct.id), int(order.id)


async def fail_manual_ticket(
    sm: async_sessionmaker[AsyncSession], *, structure_id: int, order_id: int, reason: str,
) -> None:
    """The order never reached IB: the rows say so and release what they held."""
    now = datetime.now(UTC)
    async with sm() as db:
        order = await db.get(StructureOrder, order_id)
        struct = await db.get(TradeStructure, structure_id)
        if order is not None:
            await end_order(db, order, "rejected", reason=reason, now=now)
        if struct is not None:
            struct.state = "fully_failed"
            struct.state_updated_at = now
        await db.commit()


def ticket_fields(body: Any) -> dict[str, Any]:
    return {
        "symbol": body.symbol, "sec_type": body.sec_type, "side": body.side, "qty": body.qty,
        "limit_price": body.limit_price, "expiry": body.expiry, "strike": body.strike,
        "right": body.right, "exchange": body.exchange, "currency": body.currency,
    }
