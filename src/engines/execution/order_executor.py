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
            except (TimeoutError, OSError) as e:
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
        return [trade_to_dict(t) for t in trades]

    async def list_all_trades(self) -> list[dict[str, Any]]:
        """Return every trade visible in this IB session (open + done).

        Used by the diagnostic endpoint when an order has disappeared
        from ``openTrades()`` and we need to know why — rejected /
        cancelled / filled. ``log[-1].message`` carries IB's reason
        string when the order was rejected.
        """
        ib = self._ensure()
        out: list[dict[str, Any]] = []
        for t in ib.trades():
            d = trade_to_dict(t)
            d["status"] = getattr(t.orderStatus, "status", None)
            d["last_log"] = (t.log[-1].message if t.log else None)
            out.append(d)
        return out

    # Tags conservés dans `by_currency` — sélection scale projet perso :
    # cash + valorisation + P&L. Les ~50 autres (Billable, FundValue,
    # MutualFundValue, IndianStockHaircut, ColumnPrio, etc.) sont du noise
    # IB pour notre cas d'usage et sont droppés.
    _CURRENCY_TAGS_KEEP: tuple[str, ...] = (
        "CashBalance",
        "NetLiquidationByCurrency",
        "UnrealizedPnL",
        "RealizedPnL",
        "FuturesPNL",
        "ExchangeRate",
    )

    async def account_summary(self) -> dict[str, Any]:
        """Return tous les tags numériques du compte IB, agrégés par tag.

        Beaucoup de tags ne sont exposés que dans la currency de base du
        compte (souvent EUR ou USD selon le compte) — on prend donc la
        valeur disponible dans cet ordre :
          1. BASE (= currency native du compte, agrégat propre)
          2. USD
          3. autre currency
        `by_currency` retourne un summary (6 tags clés) par currency réelle,
        sans BASE (= agrégat global, redondant avec les colonnes top-level).
        """
        ib = self._ensure()
        # Indexe les valeurs par tag puis par currency, pour pouvoir
        # appliquer la priorité BASE > USD > autres au moment du pick.
        by_tag: dict[str, dict[str, float]] = {}
        by_cur: dict[str, dict[str, float]] = {}
        account: str | None = None
        for v in ib.accountValues():
            try:
                val = float(v.value)
            except (ValueError, TypeError):
                continue
            cur = v.currency or "BASE"
            by_tag.setdefault(v.tag, {})[cur] = val
            by_cur.setdefault(cur, {})[v.tag] = val
            if account is None and v.account:
                account = v.account

        # Aplatit by_tag en out[tag] selon priorité.
        out: dict[str, Any] = {"account": account}
        for tag, cur_to_val in by_tag.items():
            for preferred in ("BASE", "USD"):
                if preferred in cur_to_val:
                    out[tag] = cur_to_val[preferred]
                    break
            else:
                out[tag] = next(iter(cur_to_val.values()))

        # by_currency : pour chaque currency réelle (≠ BASE), on filtre aux
        # tags pertinents et on drop les currencies sans aucun tag retenu.
        out["by_currency"] = {}
        for cur, vs in by_cur.items():
            if cur == "BASE":
                continue
            kept = {tag: vs[tag] for tag in self._CURRENCY_TAGS_KEEP if tag in vs}
            if kept:
                out["by_currency"][cur] = kept
        return out

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
        return trade_to_dict(trade)

    async def cancel_order(self, order_id: int) -> dict[str, Any] | None:
        ib = self._ensure()
        for t in ib.openTrades():
            if t.order.orderId == order_id:
                ib.cancelOrder(t.order)
                return trade_to_dict(t)
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
            raise ValueError("OpenPosition quantity is zero — nothing to close")
        side = "SELL" if target.position > 0 else "BUY"
        order = LimitOrder(side, qty, limit_price)
        trade = ib.placeOrder(target.contract, order)
        return trade_to_dict(trade)

    async def close_position_by_symbol(
        self,
        local_symbol: str,
        qty: int | None,
        limit_price: float | None = None,
    ) -> Any:
        """Submit a reverse order closing ``qty`` contracts of the live
        IB position matching ``local_symbol``.

        Resolution key = ``contract.localSymbol`` — same canonical id used
        by ``position_sync.py``. Lets the API tier close partial qty
        without knowing the IB ``conId``.

        Order type :
            - ``limit_price=None`` → **MarketOrder** (default for closes — fill
              immediately at touch, no price game).
            - ``limit_price=<float>`` → LimitOrder at that price (operator
              override, e.g. cleanup of a stuck close).

        ``qty=None`` means close the full open quantity.

        Returns the raw ``ib_insync`` ``Trade`` object so the caller can
        both serialize it (``trade_to_dict``) AND attach fills_handler
        callbacks (status / fill events → DB ``trade_order`` updates).
        """
        from ib_insync import LimitOrder, MarketOrder

        ib = self._ensure()
        positions = ib.positions()
        target = next(
            (p for p in positions if p.contract.localSymbol == local_symbol),
            None,
        )
        if target is None:
            raise ValueError(f"No live position with localSymbol={local_symbol!r}")
        open_qty = abs(float(target.position))
        if open_qty == 0:
            raise ValueError("OpenPosition quantity is zero — nothing to close")
        # Default = full close. Otherwise validate the requested partial qty.
        close_qty = open_qty if qty is None else float(qty)
        if close_qty <= 0:
            raise ValueError(f"close qty must be > 0 (got {qty})")
        if close_qty > open_qty:
            raise ValueError(
                f"close qty {close_qty} exceeds open qty {open_qty} for {local_symbol}"
            )
        side = "SELL" if target.position > 0 else "BUY"
        # ``ib.positions()`` returns contracts that may be missing routing
        # fields (notably ``exchange``) — IB rejects the order with
        # ``Error 321 : Missing order exchange`` if we place it as-is.
        # Re-qualify the contract so IB fills in exchange / conId / etc.
        # before routing the close.
        contract = target.contract
        if not contract.exchange:
            qualified = await ib.qualifyContractsAsync(contract)
            if not qualified:
                raise ValueError(
                    f"Contract could not be qualified for close : {local_symbol!r}",
                )
            contract = qualified[0]
        if limit_price is None:
            order = MarketOrder(side, close_qty)
        else:
            order = LimitOrder(side, close_qty, limit_price)
        return ib.placeOrder(contract, order)


def trade_to_dict(trade: Any) -> dict[str, Any]:
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
