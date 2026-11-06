"""Spot FX (secType CASH) order persistence + IB event wiring.

The Trade tab's cash ticket places EUR.USD orders through ``POST /orders``.
Until now they went straight to IB with nothing persisted : invisible in the
Orders blotter, no state after the "Sent" line, lost on refresh (issue #291).

This module keeps spot orders OUT of the structure machinery on purpose (no
booking, reaper, reservation or position-sync involvement) : one ``spot_order``
row per ticket send, written BEFORE the IB placement, then kept current by the
ib_insync ``statusEvent`` / ``fillEvent`` callbacks attached here.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engines.execution.ib_trade_log import ib_reason_from_trade, ib_why_held
from persistence.models import SpotOrder
from shared.trace import bind_trace_id, clear_trace_id, current_trace_id

logger = logging.getLogger(__name__)

# orderRef stamped on every spot order. The reaper's structure parser expects
# ``fxvol:<int>:<int>`` and returns None for this shape, so spot orders are never
# adopted onto a trade_order row.
SPOT_ORDER_REF_PREFIX = "fxvol:spot:"


def spot_order_ref(spot_order_id: int) -> str:
    return f"{SPOT_ORDER_REF_PREFIX}{spot_order_id}"


# ib_insync OrderStatus.status -> spot_order.state (None = ignore)
_STATUS_TO_STATE: dict[str, str] = {
    "PendingSubmit": "submitted",
    "ApiPending": "submitted",
    "PreSubmitted": "submitted",
    "Submitted": "submitted",
    "PendingCancel": "submitted",
    "Filled": "filled",
    "Cancelled": "cancelled",
    "ApiCancelled": "cancelled",
    "Inactive": "rejected",
}

_TERMINAL = frozenset({"filled", "cancelled", "rejected"})

# IDEALPRO minimum price variation for EUR.USD. A limit off this grid is refused
# by IB with error 110 ("price does not conform to the minimum price
# variation") and the order shows up as cancelled with no reason (#376).
SPOT_TICK = 0.00005


def map_spot_status(ib_status: str | None) -> str | None:
    """Pure : IB status string -> our state, None when it carries no transition."""
    return _STATUS_TO_STATE.get(ib_status or "")


def snap_spot_limit(price: float | None, side: str, tick: float = SPOT_TICK) -> float | None:
    """Pure : snap a limit onto the IDEALPRO tick, toward the aggressive side
    (SELL rounds down, BUY rounds up) so a marketable limit stays marketable.
    1.12258 SELL -> 1.12255 ; 1.12258 BUY -> 1.12260."""
    if price is None or price <= 0:
        return price
    steps = price / tick
    n = math.floor(steps + 1e-9) if side.upper() == "SELL" else math.ceil(steps - 1e-9)
    return round(n * tick, 5)


# ib_reason_from_trade / ib_why_held live in engines.execution.ib_trade_log
# (shared with the structure fills handler, #320) and are re-exported here.


async def create_spot_order(
    sm: async_sessionmaker[AsyncSession], *,
    symbol: str, currency: str, exchange: str, side: str, qty: int,
    limit_price: float | None,
) -> int:
    """Insert the ``pending`` row and return its id (used for the orderRef)."""
    async with sm() as db:
        row = SpotOrder(
            symbol=symbol, currency=currency, exchange=exchange, side=side,
            qty=int(qty),
            order_type="LMT" if limit_price else "MKT",
            limit_price=limit_price, state="pending",
            trace_id=current_trace_id(),
        )
        db.add(row)
        await db.commit()
        return int(row.id)


async def stamp_ib_ids(
    sm: async_sessionmaker[AsyncSession], spot_order_id: int, trade: Any,
) -> None:
    """Record IB's orderId / permId / localSymbol right after placement, and
    apply the status IB already reports on the returned Trade (the first
    statusEvent can fire before the handler is attached)."""
    o = getattr(trade, "order", None)
    c = getattr(trade, "contract", None)
    status = getattr(getattr(trade, "orderStatus", None), "status", None)
    async with sm() as db:
        row = await db.get(SpotOrder, spot_order_id)
        if row is None:
            return
        if o is not None:
            row.ib_order_id = str(getattr(o, "orderId", "") or "") or None
            row.ib_perm_id = str(getattr(o, "permId", "") or "") or None
            lmt = getattr(o, "lmtPrice", None)
            # IB may attach a price cap to a market order ; keep the price IB
            # actually works, but never the UNSET_DOUBLE sentinel.
            if lmt and float(lmt) < 1.7e308:
                row.limit_price = float(lmt)
        ls = getattr(c, "localSymbol", None)
        if ls:
            row.ib_local_symbol = str(ls)[:20]
        _apply_status(row, status, reason=ib_reason_from_trade(trade), why_held=ib_why_held(trade))
        await db.commit()


def _apply_status(
    row: SpotOrder, ib_status: str | None, *,
    reason: str | None = None, why_held: str | None = None,
) -> bool:
    """Apply an IB status to the row ; True when something changed. Filled is
    only confirmed by fills (``_on_fill``) so a stale 'Filled' status with no
    execution can't mark a row filled with qty_filled=0.

    The raw IB status (PreSubmitted vs Submitted) and ``whyHeld`` are kept on
    the row so the blotter can show WHERE a waiting order sits; a Cancelled
    that comes with an IB error in the trade log is IB refusing the order, so
    it lands as ``rejected`` with IB's text, not as a silent cancel (#376)."""
    changed = False
    if ib_status and row.ib_status != ib_status[:20]:
        row.ib_status = ib_status[:20]
        changed = True
    if (why_held or None) != row.why_held:
        row.why_held = why_held or None
        changed = True
    new_state = map_spot_status(ib_status)
    if new_state == "cancelled" and reason:
        new_state = "rejected"
    if new_state is None or new_state == "filled":
        return changed
    if row.state in _TERMINAL:
        return changed
    if new_state == "submitted" and row.state == "partially_filled":
        return changed
    if new_state == row.state:
        return changed
    row.state = new_state
    row.state_updated_at = datetime.now(UTC)
    if new_state == "rejected":
        row.rejection_text = (reason or ib_status or "")[:300]
    return True


def attach_spot_handlers(
    *, trade: Any, spot_order_id: int,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Wire the status + fill callbacks of a placed Trade onto the row."""
    trade.statusEvent += lambda t: asyncio.create_task(
        _on_status(t, spot_order_id, sessionmaker_factory)
    )
    trade.fillEvent += lambda t, fill: asyncio.create_task(
        _on_fill(t, fill, spot_order_id, sessionmaker_factory)
    )


async def _on_status(
    trade: Any, spot_order_id: int, sm: async_sessionmaker[AsyncSession],
) -> None:
    try:
        status = getattr(getattr(trade, "orderStatus", None), "status", None)
        async with sm() as db:
            row = await db.get(SpotOrder, spot_order_id)
            if row is None:
                logger.warning("spot_status_for_missing_row id=%s", spot_order_id)
                return
            if row.trace_id:
                bind_trace_id(row.trace_id)
            reason = ib_reason_from_trade(trade)
            why_held = ib_why_held(trade)
            if _apply_status(row, status, reason=reason, why_held=why_held):
                await db.commit()
                logger.info("spot_order_state id=%s ib=%s state=%s why_held=%s reason=%s",
                            spot_order_id, status, row.state, why_held, reason)
    except Exception:
        logger.exception("spot_on_status_failed id=%s", spot_order_id)
    finally:
        clear_trace_id()


async def _on_fill(
    trade: Any, fill: Any, spot_order_id: int, sm: async_sessionmaker[AsyncSession],
) -> None:
    """Fold the Trade's cumulative fills into the row (ib_insync keeps
    ``trade.fills`` complete, so a re-fold is idempotent)."""
    fills = list(getattr(trade, "fills", None) or [fill])
    await fold_spot_fills(spot_order_id, fills, sm, contract=getattr(fill, "contract", None))


async def fold_spot_fills(
    spot_order_id: int, fills: list[Any], sm: async_sessionmaker[AsyncSession],
    *, contract: Any = None,
) -> None:
    """Fold ``fills`` (the complete list for one order) into the row. Shared by
    the live ``fillEvent`` and the reconnect replay of ``reqExecutions`` (#321):
    quantities are recomputed from the full list, so replaying is idempotent."""
    try:
        qty = 0
        notional = 0.0
        commission = 0.0
        for f in fills:
            ex = f.execution
            q = int(ex.shares)
            qty += q
            notional += q * float(ex.price)
            rep = getattr(f, "commissionReport", None)
            commission += float(getattr(rep, "commission", 0.0) or 0.0)
        async with sm() as db:
            row = await db.get(SpotOrder, spot_order_id)
            if row is None:
                return
            if row.trace_id:
                bind_trace_id(row.trace_id)
            if qty < int(row.qty_filled or 0):
                # A partial replay (filter window) must never shrink what we booked.
                logger.info("spot_fold_skipped_partial id=%s seen=%s booked=%s",
                            spot_order_id, qty, row.qty_filled)
                return
            row.qty_filled = qty
            row.avg_fill_price = (notional / qty) if qty else None
            row.commission_usd = commission
            now = datetime.now(UTC)
            if qty >= row.qty:
                row.state = "filled"
                row.filled_at = row.filled_at or now
            elif qty > 0 and row.state not in _TERMINAL:
                row.state = "partially_filled"
            row.state_updated_at = now
            ls = getattr(contract, "localSymbol", None)
            if ls and not row.ib_local_symbol:
                row.ib_local_symbol = str(ls)[:20]
            await db.commit()
            logger.info("spot_order_fill id=%s filled=%s/%s avg=%s",
                        spot_order_id, qty, row.qty, row.avg_fill_price)
    except Exception:
        logger.exception("spot_on_fill_failed id=%s", spot_order_id)
    finally:
        clear_trace_id()


def parse_spot_order_ref(ref: str | None) -> int | None:
    """``fxvol:spot:<id>`` -> id ; None for anything else (pure)."""
    if not ref or not ref.startswith(SPOT_ORDER_REF_PREFIX):
        return None
    try:
        return int(ref[len(SPOT_ORDER_REF_PREFIX):])
    except ValueError:
        return None


async def adopt_open_spot_orders(
    open_trades: list[Any], sm: async_sessionmaker[AsyncSession],
) -> list[int]:
    """Startup sweep : re-attach handlers to the CASH orders IB still holds.

    Two cases, both best-effort (never blocks startup) :
      * ``orderRef=fxvol:spot:<id>`` with a matching row -> an order placed by a
        previous engine process (handlers died with it) : re-wire + sync status ;
      * any other open CASH order (placed before this feature, or by hand) ->
        create its row so it shows in the Orders blotter with a Cancel button.
    Returns the ids of the rows wired this pass.
    """
    from sqlalchemy import select

    wired: list[int] = []
    for t in open_trades:
        try:
            c = getattr(t, "contract", None)
            if (getattr(c, "secType", None) or "").upper() != "CASH":
                continue
            o = getattr(t, "order", None)
            if o is None or not hasattr(t, "statusEvent") or not hasattr(t, "fillEvent"):
                logger.warning("spot_adopt_skipped_malformed_trade")
                continue
            sid = parse_spot_order_ref(getattr(o, "orderRef", None))
            perm = str(getattr(o, "permId", "") or "") or None
            async with sm() as db:
                row = await db.get(SpotOrder, sid) if sid is not None else None
                if row is None and perm:
                    row = (await db.execute(
                        select(SpotOrder).where(SpotOrder.ib_perm_id == perm).limit(1)
                    )).scalar_one_or_none()
                if row is None:
                    lmt = getattr(o, "lmtPrice", None)
                    limit = float(lmt) if lmt and float(lmt) < 1.7e308 else None
                    row = SpotOrder(
                        symbol=str(getattr(c, "symbol", "EUR") or "EUR")[:10],
                        currency=str(getattr(c, "currency", "USD") or "USD")[:5],
                        exchange=str(getattr(c, "exchange", "IDEALPRO") or "IDEALPRO")[:10],
                        side=str(getattr(o, "action", "") or "").upper()[:5],
                        qty=int(float(getattr(o, "totalQuantity", 0) or 0)),
                        order_type="LMT" if limit else "MKT",
                        limit_price=limit, state="pending",
                    )
                    db.add(row)
                    await db.commit()
                    logger.warning("spot_order_adopted_untracked id=%s perm=%s", row.id, perm)
                sid = int(row.id)
            await stamp_ib_ids(sm, sid, t)
            attach_spot_handlers(trade=t, spot_order_id=sid, sessionmaker_factory=sm)
            wired.append(sid)
        except Exception:
            logger.exception("spot_adopt_failed")
    if wired:
        logger.info("spot_orders_rewired ids=%s", wired)
    return wired


async def mark_spot_failed(
    sm: async_sessionmaker[AsyncSession], spot_order_id: int, reason: str,
) -> None:
    """Placement raised before IB accepted anything -> the row is rejected."""
    try:
        async with sm() as db:
            row = await db.get(SpotOrder, spot_order_id)
            if row is None:
                return
            row.state = "rejected"
            row.rejection_text = reason[:300]
            row.state_updated_at = datetime.now(UTC)
            await db.commit()
    except Exception:
        logger.exception("spot_mark_failed_failed id=%s", spot_order_id)
