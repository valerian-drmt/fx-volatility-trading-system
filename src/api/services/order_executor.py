"""Wrapper async autour de ib_insync pour place/cancel/close depuis l'api.

Connexion IB partagée par le lifespan FastAPI (un seul `IB` instance,
clientId=4). Toutes les méthodes sont safe à appeler concurremment dans
l'event loop FastAPI (ib_insync utilise asyncio en interne).

Ce service est utilisé par `api.routers.orders`. Si la connexion IB est
DOWN (Gateway pas joignable, TrustedIPs KO), les endpoints renvoient 503.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OrderRequest:
    """Payload normalisé pour place/close. Construit depuis le body API."""
    symbol: str               # e.g. "EUR" pour FUT, "EUU" pour FOP EUR options
    sec_type: str             # "FUT" | "FOP"
    side: str                 # "BUY" | "SELL"
    qty: int
    limit_price: float
    expiry: str | None = None    # YYYYMMDD pour FUT/FOP
    strike: float | None = None  # FOP only
    right: str | None = None     # "C" | "P", FOP only
    exchange: str = "CME"
    currency: str = "USD"
    trading_class: str | None = None  # e.g. "EUU" pour FOP EUR


class OrderExecutorUnavailable(RuntimeError):
    """Levée si l'IB connection est down — endpoint orders renvoie 503."""


class OrderExecutor:
    """One IB connection, shared across requests. Async-safe via ib_insync."""

    def __init__(self, host: str, port: int, client_id: int) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self._ib: Any | None = None
        self._lock = asyncio.Lock()

    async def connect(self, timeout: float = 5.0) -> None:
        """Open the IB connection. Idempotent (ne reconnecte pas si déjà OK)."""
        from ib_insync import IB

        async with self._lock:
            if self._ib is not None and self._ib.isConnected():
                return
            ib = IB()
            try:
                await asyncio.wait_for(
                    ib.connectAsync(self.host, self.port, clientId=self.client_id),
                    timeout=timeout,
                )
            except (TimeoutError, asyncio.TimeoutError, OSError) as e:
                logger.warning("ib_connect_failed: %s", e)
                self._ib = None
                return
            self._ib = ib
            logger.info("ib_connected host=%s port=%s clientId=%s", self.host, self.port, self.client_id)

    async def disconnect(self) -> None:
        async with self._lock:
            if self._ib is not None and self._ib.isConnected():
                self._ib.disconnect()
            self._ib = None

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def _ensure(self) -> Any:
        if not self.is_connected():
            raise OrderExecutorUnavailable("IB Gateway not connected")
        return self._ib

    # ---- Read operations ----------------------------------------------------

    async def list_open_orders(self) -> list[dict[str, Any]]:
        """Return all currently-open trades (orders not yet filled/cancelled)."""
        ib = self._ensure()
        trades = ib.openTrades()
        return [_trade_to_dict(t) for t in trades]

    async def list_positions(self) -> list[dict[str, Any]]:
        """Return live positions from IB (= broker truth, not our DB cache)."""
        ib = self._ensure()
        positions = ib.positions()
        out = []
        for p in positions:
            c = p.contract
            out.append({
                "account": p.account,
                "symbol": c.symbol,
                "sec_type": c.secType,
                "expiry": c.lastTradeDateOrContractMonth or None,
                "strike": c.strike or None,
                "right": c.right or None,
                "exchange": c.exchange,
                "currency": c.currency,
                "local_symbol": c.localSymbol,
                "con_id": c.conId,
                "position": float(p.position),
                "avg_cost": float(p.avgCost),
            })
        return out

    # ---- Mutation operations ------------------------------------------------

    async def place_order(self, req: OrderRequest) -> dict[str, Any]:
        from ib_insync import Contract, LimitOrder

        ib = self._ensure()
        contract = Contract(
            symbol=req.symbol,
            secType=req.sec_type,
            exchange=req.exchange,
            currency=req.currency,
        )
        if req.expiry:
            contract.lastTradeDateOrContractMonth = req.expiry
        if req.strike is not None:
            contract.strike = req.strike
        if req.right:
            contract.right = req.right
        if req.trading_class:
            contract.tradingClass = req.trading_class

        # Qualify the contract so IB resolves conId/multiplier before placing.
        qualified = await ib.qualifyContractsAsync(contract)
        if not qualified:
            raise ValueError(f"Contract not qualified by IB: {contract}")
        contract = qualified[0]

        order = LimitOrder(req.side, req.qty, req.limit_price)
        trade = ib.placeOrder(contract, order)
        # Don't wait for fill — return immediately. UI peut poll openOrders.
        return _trade_to_dict(trade)

    async def cancel_order(self, order_id: int) -> dict[str, Any] | None:
        ib = self._ensure()
        for t in ib.openTrades():
            if t.order.orderId == order_id:
                ib.cancelOrder(t.order)
                return _trade_to_dict(t)
        return None

    async def close_position(self, con_id: int, limit_price: float) -> dict[str, Any]:
        """Submit a reverse LimitOrder to close the position identified by `con_id`."""
        from ib_insync import LimitOrder

        ib = self._ensure()
        positions = ib.positions()
        target = next((p for p in positions if p.contract.conId == con_id), None)
        if target is None:
            raise ValueError(f"No live position with conId={con_id}")
        qty = abs(float(target.position))
        if qty == 0:
            raise ValueError("Position quantity is zero — nothing to close")
        side = "SELL" if target.position > 0 else "BUY"
        order = LimitOrder(side, qty, limit_price)
        trade = ib.placeOrder(target.contract, order)
        return _trade_to_dict(trade)


def _trade_to_dict(trade: Any) -> dict[str, Any]:
    """Sérialise un Trade ib_insync en dict JSON-safe."""
    o = trade.order
    c = trade.contract
    s = trade.orderStatus
    return {
        "order_id": o.orderId,
        "perm_id": o.permId,
        "symbol": c.symbol,
        "sec_type": c.secType,
        "expiry": c.lastTradeDateOrContractMonth or None,
        "strike": c.strike or None,
        "right": c.right or None,
        "local_symbol": c.localSymbol,
        "con_id": c.conId,
        "side": o.action,
        "qty": float(o.totalQuantity),
        "limit_price": float(o.lmtPrice) if o.lmtPrice else None,
        "status": s.status,
        "filled": float(s.filled),
        "remaining": float(s.remaining),
        "avg_fill_price": float(s.avgFillPrice) if s.avgFillPrice else None,
    }
