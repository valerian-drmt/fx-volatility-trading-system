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

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
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
        orders = (await db.execute(
            select(StructureOrder)
            .where(StructureOrder.structure_id == structure_id)
            .where(StructureOrder.order_role == "entry")
            .order_by(StructureOrder.leg_idx)
        )).scalars().all()
        if not orders:
            raise LiveSubmitError(f"structure {structure_id} has no entry orders")

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
            conid_to_order: dict[int, int] = {}
            combo_input: list[dict[str, Any]] = []
            for order in orders:
                if order.contract_expiry is None or order.contract_strike is None:
                    raise LiveSubmitError(f"order {order.id} missing expiry/strike for combo")
                if order.limit_price is None:
                    raise LiveSubmitError(f"order {order.id} missing limit_price for combo")
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
                    "limit_price": float(order.limit_price),
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
            combo_order = LimitOrder(co["action"], co["totalQuantity"], co["lmtPrice"])
            combo_order.tif = first.time_in_force or "DAY"
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
            db.add(TradeEvent(
                structure_id=structure_id, event_type="live_submit_combo", severity="info",
                description=f"placed BAG combo net={co['lmtPrice']} base_qty={co['totalQuantity']}",
                payload={"net_price": co["lmtPrice"], "base_qty": co["totalQuantity"], "n_legs": len(orders)},
            ))
            await db.commit()
            return {
                "structure_id": structure_id, "n_orders_placed": len(orders),
                "combo": True, "combo_eligible": combo_eligible,
                "orders": [{"leg_idx": o.leg_idx, "order_id": o.id, "ib_order_id": o.ib_order_id} for o in orders],
            }

        # ── Per-leg path (default / futures / non-combo) ──
        placed: list[dict[str, Any]] = []
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
            contract = qualified[0]

            tif = order.time_in_force or "DAY"
            if is_market:
                # MarketOrder takes only (action, totalQuantity).
                ib_order = MarketOrder(order.side, int(order.qty))
                ib_order.tif = tif
            else:
                ok = build_order_kwargs(
                    side=order.side, qty=order.qty,
                    limit_price=float(order.limit_price),
                    time_in_force=tif,
                )
                ib_order = LimitOrder(ok["action"], ok["totalQuantity"], ok["lmtPrice"])
                ib_order.tif = ok["tif"]

            trade = ib.placeOrder(contract, ib_order)
            attach_fill_handlers(
                trade=trade, order_id=order.id, sessionmaker_factory=sm,
            )

            order.ib_order_id = str(trade.order.orderId)
            order.ib_perm_id = str(trade.order.permId) if trade.order.permId else None
            order.state = "submitted"
            order.submitted_at = datetime.now(UTC)
            placed.append({
                "leg_idx": order.leg_idx,
                "order_id": order.id,
                "ib_order_id": order.ib_order_id,
            })
        await db.commit()

    return {
        "structure_id": structure_id,
        "n_orders_placed": len(placed),
        "combo_eligible": combo_eligible,
        "orders": placed,
    }
