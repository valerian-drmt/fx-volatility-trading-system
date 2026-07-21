"""Live execution path — replaces the mock submit when execution_mode='live'.

API flow
--------
1. ``api.routers.trade.submit_preview`` (execution_mode='live') :
     - persists ``trade_structures`` (state='submitted') + ``structure_orders``
       (state='pending', limit_price already computed)
     - then HTTP-POSTs ``execution-engine:8001/internal/structure/submit``
       with the new ``structure_id``.
2. This module reads the orders, places them via ib_insync, attaches event
   handlers (``_on_order_status`` / ``_on_execution``), and returns a stub
   summary. Persistence of fills + cascade to ``trade_positions`` happens
   asynchronously inside the event callbacks (cf. ``fills_handler.py``).

Combo support
-------------
``can_use_combo`` is checked. When ``use_combo=True`` (env ``EXECUTION_USE_COMBO``)
and the legs are combo-eligible options, they are placed as a single IB BAG so the
structure fills all-or-nothing (no naked half-fill — cf. the RR whose put filled
while the call didn't). Otherwise (default, futures, or a single leg) we fall back
to one order per leg. Combo assembly lives in ``core.execution.build_combo``.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.execution.contract_builder import (
    build_combo,
    build_contract_kwargs,
    build_order_kwargs,
    can_use_combo,
)
from engines.execution.fills_handler import (
    attach_combo_fill_handlers,
    attach_fill_handlers,
)
from persistence.models import (
    StructureOrder,
    TradeEvent,
    TradeStructure,
)

logger = logging.getLogger(__name__)


class LiveSubmitError(RuntimeError):
    """Raised when live submit cannot proceed (IB down, contract unqualified)."""


class LiveSubmitAlreadyClaimed(LiveSubmitError):
    """The structure's submit claim is already taken (EXEC-1).

    A previous (or concurrent) live-submit call owns this structure — the
    replay must NOT place anything. Mapped to HTTP 409 by the engine endpoint
    so the API layer can treat it as submit-already-in-progress rather than a
    hard failure."""

    def __init__(self, structure_id: int) -> None:
        super().__init__(
            f"structure {structure_id} already claimed for live submit "
            "(idempotent replay refused)"
        )


_OPT_TICK = 0.0001  # CME EUR-FOP minimum price variation


def order_ref(structure_id: int, order_id: int) -> str:
    """Durable idempotency key stamped on every IB order (``Order.orderRef``).

    IB persists orderRef and returns it on open-order/execution queries, so an
    orphan (crash between ``placeOrder`` and the per-leg commit) stays
    discoverable and is adopted back onto its DB row by the reaper sweep
    (cf. ``engines.execution.reaper``)."""
    return f"fxvol:{structure_id}:{order_id}"


async def _release_submit_claim(
    db: AsyncSession, structure_id: int, reason: str,
) -> None:
    """Clear ``submit_claimed_at`` after a failure that placed ZERO orders, so
    a genuine retry stays possible. Rolls back uncommitted mutations first."""
    await db.rollback()
    await db.execute(
        update(TradeStructure)
        .where(TradeStructure.id == structure_id)
        .values(submit_claimed_at=None)
    )
    db.add(TradeEvent(
        structure_id=structure_id,
        event_type="live_submit_aborted", severity="warning",
        description=f"live submit aborted before any placement: {reason[:200]}",
        payload={"reason": reason[:300]},
    ))
    await db.commit()


def _num(x: Any) -> float | None:
    """Coerce to a positive finite float, else None (drops NaN / <=0)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v > 0 else None


def marketable_from_quote(
    side: str,
    bid: float | None,
    ask: float | None,
    mkt: float | None,
    fallback: float,
    tick: float = _OPT_TICK,
) -> float:
    """Pure: marketable limit at the touch (BUY → ask, SELL → bid), snapped to the
    tick. Falls back to the theoretical ``fallback`` (preview premium) only when the
    live quote's relevant side is absent — a BUY fallback sits BELOW the ask and a
    SELL fallback ABOVE the bid, so neither crosses; that non-marketable rest is
    exactly why a leg with no live quote hangs 'submitted' for a long time."""
    if side.upper() == "BUY":
        ref = ask or mkt
        return round(math.ceil(ref / tick) * tick, 6) if ref else fallback
    ref = bid or mkt
    if not ref:
        return fallback
    lp = math.floor(ref / tick) * tick
    return round(lp, 6) if lp > 0 else fallback


async def _live_quote(
    ib: Any, contract: Any, *, attempts: int = 3, gap_s: float = 0.3, timeout_s: float = 2.0,
) -> tuple[float | None, float | None, float | None]:
    """Fetch (bid, ask, mkt) for an option, RETRYING until a side populates. A cold
    market-data line — a leg's strike that never streamed before, the common case
    for the 2nd+ leg of a multi-leg order (a strangle/spread) — returns nothing on
    the first snapshot within the timeout, so a single try leaves the leg to fall
    back to a non-marketable theoretical limit and hang. Warming it with a few
    retries lets the leg price at the live touch and fill like a standalone."""
    for i in range(attempts):
        try:
            tickers = await asyncio.wait_for(ib.reqTickersAsync(contract), timeout=timeout_s)
        except Exception:
            tickers = None
        t = tickers[0] if tickers else None
        bid = _num(getattr(t, "bid", None)) if t is not None else None
        ask = _num(getattr(t, "ask", None)) if t is not None else None
        mkt = None
        if t is not None:
            try:
                mkt = _num(t.marketPrice())
            except Exception:
                mkt = None
        if bid is not None or ask is not None or mkt is not None:
            return bid, ask, mkt
        if i < attempts - 1:
            await asyncio.sleep(gap_s)
    return None, None, None


async def _marketable_limit(ib: Any, contract: Any, side: str, fallback: float) -> float:
    """Price a marketable limit off IB's LIVE quote so the order fills at the touch
    (BUY → ask, SELL → bid), snapped to the tick. The theoretical preview premium
    misprices real options (esp. OTM), so a limit at it never crosses. Warms the
    quote with retries first (see ``_live_quote``) before falling back to it."""
    bid, ask, mkt = await _live_quote(ib, contract)
    if ask is None and bid is None and mkt is None:
        logger.warning(
            "live_submit_no_quote side=%s localSymbol=%s -> resting at theoretical "
            "limit (may fill slowly)", side, getattr(contract, "localSymbol", "?"),
        )
    return marketable_from_quote(side, bid, ask, mkt, fallback)


async def submit_structure_live(
    *,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    executor: Any,                  # OrderExecutor
    structure_id: int,
    use_combo: bool = False,
) -> dict[str, Any]:
    """Place a structure's entry orders and wire fills handlers.

    ``use_combo`` (off by default) : if the legs are combo-eligible options, place
    them as a single IB BAG so they fill all-or-nothing (no naked half-fill).
    Otherwise (or for futures) fall back to one order per leg. Returns a summary
    dict — final state arrives via events, not via this call.
    """
    if not executor.is_connected():
        raise LiveSubmitError("IB Gateway not connected")

    from ib_insync import ComboLeg, Contract, LimitOrder, MarketOrder

    sm = sessionmaker_factory
    async with sm() as db:
        struct = (await db.execute(
            select(TradeStructure).where(TradeStructure.id == structure_id).limit(1)
        )).scalar_one_or_none()
        if struct is None:
            raise LiveSubmitError(f"structure {structure_id} not found")
        if struct.state != "submitted":
            # Belt for replays after fills already started driving the FSM.
            raise LiveSubmitError(
                f"structure {structure_id} in state '{struct.state}' — "
                "live submit only allowed from 'submitted'"
            )

        # ── Idempotency claim (EXEC-1) ── atomic UPDATE ... WHERE claim IS
        # NULL, committed (durable) BEFORE anything touches IB. A replayed
        # call loses the race and is refused — it can never re-place legs.
        claimed = (await db.execute(
            update(TradeStructure)
            .where(
                TradeStructure.id == structure_id,
                TradeStructure.submit_claimed_at.is_(None),
            )
            .values(submit_claimed_at=datetime.now(UTC))
            .returning(TradeStructure.id)
        )).scalar_one_or_none()
        await db.commit()
        if claimed is None:
            raise LiveSubmitAlreadyClaimed(structure_id)

        # Pending-leg filter (EXEC-1): only never-placed entry legs are
        # eligible — a leg that already carries an ib_order_id is live at IB.
        orders = (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.structure_id == structure_id)
            .where(StructureOrder.order_role == "entry")
            .where(StructureOrder.state == "pending")
            .where(StructureOrder.ib_order_id.is_(None))
            .order_by(StructureOrder.leg_idx)
        )).scalars().all()
        if not orders:
            n_entry = len((await db.execute(
                select(StructureOrder.id)
                .where(StructureOrder.structure_id == structure_id)
                .where(StructureOrder.order_role == "entry")
            )).scalars().all())
            if n_entry == 0:
                await _release_submit_claim(db, structure_id, "no entry orders")
                raise LiveSubmitError(f"structure {structure_id} has no entry orders")
            # All legs already placed — idempotent success, nothing to do.
            logger.info(
                "live_submit_noop structure_id=%s all %s entry legs already placed",
                structure_id, n_entry,
            )
            return {
                "structure_id": structure_id, "n_orders_placed": 0,
                "noop": True, "orders": [],
            }

        # Combo detection (informational for V1 — we still issue separate orders).
        legs_meta = [
            {
                "expiry": o.contract_expiry,
                "contract_symbol": o.contract_symbol,
                "contract_exchange": o.contract_exchange,
                "contract_currency": o.contract_currency,
            }
            for o in orders
        ]
        combo_eligible = can_use_combo(legs_meta)
        db.add(TradeEvent(
            structure_id=structure_id,
            event_type="live_submit_attempt", severity="info",
            description=f"live submit ; combo_eligible={combo_eligible}",
            payload={"n_orders": len(orders), "combo_eligible": combo_eligible},
        ))

        ib = executor._ensure()

        # ── Combo (BAG) path — atomic all-or-nothing fill for multi-leg options ──
        all_options = all(o.contract_type.lower() != "future" for o in orders)
        if use_combo and combo_eligible and all_options and len(orders) >= 2:
            try:
                conid_to_order: dict[int, int] = {}
                combo_input: list[dict[str, Any]] = []
                for order in orders:
                    if order.contract_expiry is None or order.contract_strike is None:
                        raise LiveSubmitError(f"order {order.id} missing expiry/strike for combo")
                    ck = build_contract_kwargs(
                        contract_type=order.contract_type, expiry=order.contract_expiry,
                        strike=float(order.contract_strike), symbol=order.contract_symbol,
                        exchange=order.contract_exchange, currency=order.contract_currency,
                    )
                    qualified = await ib.qualifyContractsAsync(Contract(**ck))
                    if not qualified:
                        raise LiveSubmitError(f"combo leg not qualified for order {order.id} (sent: {ck})")
                    qc = qualified[0]
                    conid_to_order[int(qc.conId)] = order.id
                    combo_input.append({
                        "conId": int(qc.conId), "side": order.side, "qty": int(order.qty),
                        # None when the desk sends the leg as a market order → market BAG
                        "limit_price": float(order.limit_price) if order.limit_price is not None else None,
                        "exchange": getattr(qc, "exchange", None) or order.contract_exchange,
                    })
                first = orders[0]
                combo = build_combo(
                    symbol=first.contract_symbol, exchange=first.contract_exchange,
                    currency=first.contract_currency, legs=combo_input,
                )
                cc = combo["contract"]  # type: ignore[index]
                co = combo["order"]     # type: ignore[index]
                bag = Contract(
                    symbol=cc["symbol"], secType="BAG", exchange=cc["exchange"], currency=cc["currency"],
                    comboLegs=[ComboLeg(**cl) for cl in cc["comboLegs"]],
                )
                # LimitOrder only when every leg had a price ; otherwise a market BAG
                # (matches the desk sending legs as MKT — see api trade.submit).
                if "lmtPrice" in co:
                    combo_order = LimitOrder(co["action"], co["totalQuantity"], co["lmtPrice"])
                else:
                    combo_order = MarketOrder(co["action"], co["totalQuantity"])
                combo_order.tif = first.time_in_force or "DAY"
                # One BAG = one IB order; key it on the first leg row (EXEC-1).
                combo_order.orderRef = order_ref(structure_id, first.id)
            except Exception as e:
                # Nothing reached IB — release the claim so a retry is allowed.
                await _release_submit_claim(
                    db, structure_id, f"combo build/qualify failed: {e}",
                )
                raise
            trade = ib.placeOrder(bag, combo_order)
            attach_combo_fill_handlers(
                trade=trade, conid_to_order=conid_to_order, sessionmaker_factory=sm,
            )
            now = datetime.now(UTC)
            for order in orders:
                order.ib_order_id = str(trade.order.orderId)
                order.ib_perm_id = str(trade.order.permId) if trade.order.permId else None
                order.state = "submitted"
                order.submitted_at = now
            net_price = co.get("lmtPrice")  # None for a market BAG
            db.add(TradeEvent(
                structure_id=structure_id, event_type="live_submit_combo", severity="info",
                description=f"placed BAG combo net={net_price if net_price is not None else 'MKT'} base_qty={co['totalQuantity']}",
                payload={"net_price": net_price, "base_qty": co["totalQuantity"], "n_legs": len(orders)},
            ))
            await db.commit()
            return {
                "structure_id": structure_id, "n_orders_placed": len(orders),
                "combo": True, "combo_eligible": combo_eligible,
                "orders": [{"leg_idx": o.leg_idx, "order_id": o.id, "ib_order_id": o.ib_order_id} for o in orders],
            }

        # ── Per-leg path (default / futures / non-combo) ──
        # Phase 1 (EXEC-2) — NO side effects: validate + qualify EVERY leg
        # before placing ANY. Qualification failure (the dominant real-world
        # failure) is a zero-orders-placed failure with the claim released.
        prepared: list[tuple[StructureOrder, Any, bool]] = []
        try:
            for order in orders:
                is_future = order.contract_type.lower() == "future"
                is_market = (order.order_type or "LMT").upper() == "MKT"
                if order.contract_expiry is None:
                    raise LiveSubmitError(
                        f"order {order.id} missing expiry — cannot build contract"
                    )
                if not is_future and order.contract_strike is None:
                    raise LiveSubmitError(
                        f"order {order.id} missing strike — cannot build option contract"
                    )
                if not is_market and order.limit_price is None:
                    raise LiveSubmitError(f"order {order.id} missing limit_price")

                ck = build_contract_kwargs(
                    contract_type=order.contract_type,
                    expiry=order.contract_expiry,
                    strike=float(order.contract_strike) if order.contract_strike is not None else None,
                    symbol=order.contract_symbol,
                    exchange=order.contract_exchange,
                    currency=order.contract_currency,
                )
                logger.info(
                    "live_submit_build_contract order=%s kwargs=%s",
                    order.id, ck,
                )
                contract = Contract(**ck)
                qualified = await ib.qualifyContractsAsync(contract)
                logger.info(
                    "live_submit_qualify_result order=%s n_qualified=%s contracts=%s",
                    order.id, len(qualified),
                    [{"conId": c.conId, "localSymbol": c.localSymbol,
                      "exchange": c.exchange, "tradingClass": c.tradingClass}
                     for c in qualified[:3]],
                )
                if not qualified:
                    # Fallback : if FUT failed with exchange='CME', retry with
                    # 'GLOBEX' (legacy alias still recognised by some accounts).
                    if is_future and ck.get("exchange") == "CME":
                        ck_retry = {**ck, "exchange": "GLOBEX"}
                        logger.info(
                            "live_submit_retry_globex order=%s kwargs=%s",
                            order.id, ck_retry,
                        )
                        qualified = await ib.qualifyContractsAsync(Contract(**ck_retry))
                        logger.info(
                            "live_submit_qualify_retry order=%s n_qualified=%s",
                            order.id, len(qualified),
                        )
                    if not qualified:
                        raise LiveSubmitError(
                            f"contract not qualified for order {order.id} "
                            f"(sent: {ck})"
                        )
                prepared.append((order, qualified[0], is_market))
        except Exception as e:
            # Zero orders placed — release the claim so a retry is allowed.
            await _release_submit_claim(
                db, structure_id, f"pre-placement failure: {e}",
            )
            raise

        # Phase 2 (EXEC-2) — place then COMMIT PER LEG. A failure at leg k can
        # no longer roll back legs 1..k-1 that are already live at IB; the
        # residual window (crash between placeOrder and its commit) is closed
        # by the orderRef adoption sweep in the reaper.
        placed: list[dict[str, Any]] = []
        try:
            for order, contract, is_market in prepared:
                tif = order.time_in_force or "DAY"
                if is_market:
                    # MarketOrder takes only (action, totalQuantity).
                    ib_order = MarketOrder(order.side, int(order.qty))
                    ib_order.tif = tif
                else:
                    # Re-price the marketable limit off IB's LIVE quote
                    # (BUY→ask, SELL→bid) so it actually crosses ; the stored
                    # preview limit is only a fallback when no quote exists.
                    lp = await _marketable_limit(
                        ib, contract, order.side, float(order.limit_price),
                    )
                    order.limit_price = lp
                    ok = build_order_kwargs(
                        side=order.side, qty=order.qty, limit_price=lp, time_in_force=tif,
                    )
                    ib_order = LimitOrder(ok["action"], ok["totalQuantity"], ok["lmtPrice"])
                    ib_order.tif = ok["tif"]
                # Durable idempotency key (EXEC-1) — survives at IB across
                # restarts; the reaper adopts orphans by matching it.
                ib_order.orderRef = order_ref(structure_id, order.id)

                trade = ib.placeOrder(contract, ib_order)
                order.ib_order_id = str(trade.order.orderId)
                order.ib_perm_id = str(trade.order.permId) if trade.order.permId else None
                order.state = "submitted"
                order.submitted_at = datetime.now(UTC)
                leg_summary = {
                    "leg_idx": order.leg_idx,
                    "order_id": order.id,
                    "ib_order_id": order.ib_order_id,
                }
                await db.commit()   # per-leg durability — DB never lags IB
                attach_fill_handlers(
                    trade=trade, order_id=leg_summary["order_id"],
                    sessionmaker_factory=sm,
                )
                placed.append(leg_summary)
        except Exception as e:
            # Legs already placed are committed and stay visible. Mark the
            # structure partial_fail so reaper/rollback see a truthful picture.
            await db.rollback()
            failed_struct = await db.get(TradeStructure, structure_id)
            if failed_struct is not None:
                failed_struct.state = "partial_fail"
                failed_struct.state_updated_at = datetime.now(UTC)
            db.add(TradeEvent(
                structure_id=structure_id,
                event_type="live_submit_partial_failure", severity="error",
                description=(
                    f"live submit failed after {len(placed)}/{len(prepared)} "
                    f"legs placed: {str(e)[:200]}"
                ),
                payload={
                    "n_placed": len(placed), "n_total": len(prepared),
                    "placed": placed, "error": str(e)[:300],
                },
            ))
            await db.commit()
            raise LiveSubmitError(
                f"live submit failed after {len(placed)}/{len(prepared)} legs "
                f"placed: {e}"
            ) from e

    return {
        "structure_id": structure_id,
        "n_orders_placed": len(placed),
        "combo_eligible": combo_eligible,
        "orders": placed,
    }
