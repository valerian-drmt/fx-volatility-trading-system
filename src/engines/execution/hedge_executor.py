"""Live submission of a delta-hedge `HedgeOrder` row.

Called by ``POST /internal/hedge`` after the position-monitor created a
``hedge_orders`` row in state='pending'. Places an EUR/USD CME future
LMT order via the shared ``OrderExecutor``, attaches a fill callback that
finalises the row (state='filled', fill_price, total_cost_usd).

Spec : ``docs/vol_trading_pca/specs/STEP5_ACTIVE_POSITIONS.md`` §9.4.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.models import HedgeOrder

logger = logging.getLogger(__name__)

_HEDGE_SYMBOL = "EUR"
_HEDGE_EXCHANGE = "CME"
_HEDGE_CURRENCY = "USD"
_HEDGE_SECTYPE = "FUT"


class HedgeSubmitError(RuntimeError):
    """Raised when /internal/hedge cannot proceed."""


async def submit_hedge_order(
    *,
    sessionmaker_factory: async_sessionmaker[AsyncSession],
    executor: Any,                  # OrderExecutor
    hedge_order_id: int,
    front_month_expiry: str | None = None,
    limit_price: float | None = None,
) -> dict[str, Any]:
    """Place the front-month EUR FUT for a hedge row in state='pending'.

    Parameters
    ----------
    front_month_expiry : str | None
        IB ``YYYYMM`` (or YYYYMMDD) tag for the front-month contract. ``None``
        triggers a basic IB lookup via reqContractDetails.
    limit_price : float | None
        If ``None`` we fetch the best ask (BUY) / bid (SELL) from IB. Real
        markets : caller may pass a tighter limit.
    """
    if not executor.is_connected():
        raise HedgeSubmitError("IB Gateway not connected")

    from ib_insync import Contract, LimitOrder

    sm = sessionmaker_factory
    async with sm() as db:
        hedge = await db.get(HedgeOrder, hedge_order_id)
        if hedge is None:
            raise HedgeSubmitError(f"hedge_order {hedge_order_id} not found")
        if hedge.state != "pending":
            return {
                "hedge_order_id": hedge_order_id,
                "state": hedge.state,
                "skipped": True,
            }

        ib = executor._ensure()

        # Build EUR/USD front-month futures contract.
        contract = Contract(
            symbol=_HEDGE_SYMBOL, secType=_HEDGE_SECTYPE,
            exchange=_HEDGE_EXCHANGE, currency=_HEDGE_CURRENCY,
        )
        if front_month_expiry:
            contract.lastTradeDateOrContractMonth = front_month_expiry

        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            hedge.state = "failed"
            await db.commit()
            raise HedgeSubmitError(
                f"hedge contract not qualified (symbol={_HEDGE_SYMBOL}, FUT)"
            )
        contract = qualified[0]

        # Pick a limit if not supplied. Fall back to mid spot if no quote.
        lmt = limit_price
        if lmt is None:
            try:
                ticker = ib.reqMktData(contract, "", False, False)
                # Allow ib_insync a moment to deliver the snapshot.
                await asyncio.sleep(0.5)
                quote = ticker.ask if hedge.side == "BUY" else ticker.bid
                lmt = float(quote) if quote and quote > 0 else None
            except Exception:
                lmt = None
        if lmt is None or lmt <= 0:
            hedge.state = "failed"
            await db.commit()
            raise HedgeSubmitError("no valid limit price for hedge order")

        ib_order = LimitOrder(hedge.side, hedge.hedge_qty, lmt)
        trade = ib.placeOrder(contract, ib_order)
        hedge.ib_order_id = str(trade.order.orderId)
        hedge.submitted_at = datetime.now(UTC)
        hedge.state = "submitted"

        # Wire a single fill callback to finalise the row.
        _BG_TASKS: set[asyncio.Task] = set()

        def _on_hedge_status(t: Any) -> None:
            task = asyncio.create_task(_finalise_hedge(sm, hedge_order_id, t))
            _BG_TASKS.add(task)
            task.add_done_callback(_BG_TASKS.discard)

        trade.statusEvent += _on_hedge_status

        await db.commit()
        return {
            "hedge_order_id": hedge_order_id,
            "ib_order_id": hedge.ib_order_id,
            "limit_price": lmt,
            "state": hedge.state,
        }


async def _finalise_hedge(
    sm: async_sessionmaker[AsyncSession],
    hedge_order_id: int,
    trade: Any,
) -> None:
    """Mark the hedge row state='filled' once IB reports `Filled`.

    fill_price = avgFillPrice ; commission_usd from the commission report
    (IB exposes it on `trade.fills[i].commissionReport`) ; total_cost_usd is
    the unsigned absolute commission for V1 (spread later).
    """
    try:
        if trade.orderStatus.status != "Filled":
            return
        async with sm() as db:
            hedge = await db.get(HedgeOrder, hedge_order_id)
            if hedge is None or hedge.state == "filled":
                return
            avg = float(trade.orderStatus.avgFillPrice or 0.0)
            commission = 0.0
            try:
                for f in getattr(trade, "fills", []) or []:
                    commission += float(getattr(
                        f.commissionReport, "commission", 0.0,
                    ) or 0.0)
            except Exception:
                pass
            hedge.fill_price = avg if avg > 0 else None
            hedge.commission_usd = commission
            hedge.spread_paid_usd = 0.0
            hedge.total_cost_usd = commission
            hedge.filled_at = datetime.now(UTC)
            hedge.state = "filled"
            await db.commit()
    except Exception:
        logger.exception("finalise_hedge_failed id=%s", hedge_order_id)
