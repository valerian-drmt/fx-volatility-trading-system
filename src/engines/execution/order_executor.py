"""Async wrapper around ib_insync for place/cancel/close from the api.

IB connection shared by the FastAPI lifespan (a single `IB` instance,
clientId=4). All methods are safe to call concurrently inside the
FastAPI event loop (ib_insync uses asyncio internally).

Used by `api.routers.orders`. If the IB connection is DOWN (Gateway
unreachable, TrustedIPs broken), the endpoints return 503.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_OPT_TICK = 0.0001  # CME EUR-FOP minimum price variation


def _pos_num(x: Any) -> float | None:
    """Positive finite float, else None (drops NaN / <=0)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v == v and v > 0 else None


async def _marketable_close_price(ib: Any, contract: Any, side: str) -> float | None:
    """Marketable close limit off IB's LIVE quote (SELL→bid, BUY→ask), tick-snapped.
    Returns None when no quote is available → caller uses a plain market order.
    Options need this : a market close hits IB's price-cap and hangs 'submitted'."""
    try:
        tickers = await asyncio.wait_for(ib.reqTickersAsync(contract), timeout=3.0)
    except Exception:
        tickers = None
    t = tickers[0] if tickers else None
    bid = _pos_num(getattr(t, "bid", None)) if t is not None else None
    ask = _pos_num(getattr(t, "ask", None)) if t is not None else None
    mkt = None
    if t is not None:
        try:
            mkt = _pos_num(t.marketPrice())
        except Exception:
            mkt = None
    if side.upper() == "BUY":
        ref = ask or mkt
        return round(math.ceil(ref / _OPT_TICK) * _OPT_TICK, 6) if ref else None
    ref = bid or mkt
    if not ref:
        return None
    lp = math.floor(ref / _OPT_TICK) * _OPT_TICK
    return round(lp, 6) if lp > 0 else None


@dataclass
class OrderRequest:
    """Normalized payload for place/close. Built from the API body."""
    symbol: str               # e.g. "EUR" for FUT/CASH, "EUU" for FOP EUR options
    sec_type: str             # "FUT" | "FOP" | "CASH" (spot FX, e.g. EUR.USD)
    side: str                 # "BUY" | "SELL"
    qty: int
    limit_price: float | None    # None ⇒ MarketOrder (spot cash management)
    expiry: str | None = None    # YYYYMMDD for FUT/FOP
    strike: float | None = None  # FOP only
    right: str | None = None     # "C" | "P", FOP only
    exchange: str = "CME"        # "IDEALPRO" for CASH
    currency: str = "USD"
    trading_class: str | None = None  # e.g. "EUU" for FOP EUR


class OrderExecutorUnavailable(RuntimeError):
    """Raised when the IB connection is down — orders endpoints return 503."""


class OrderExecutor:
    """One IB connection, shared across requests. Async-safe via ib_insync."""

    def __init__(
        self, host: str, port: int, client_id: int, readonly: bool = False
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        # Mirrors the gateway's own READ_ONLY_API setting. When the gateway runs
        # read-only, ib_insync's default handshake calls reqAutoOpenOrders, IB
        # rejects it ("Error 321 ... The API interface is currently in Read-Only
        # mode") and the connect never completes — so the engine stays down
        # instead of degrading to reads. Passing readonly through skips that call.
        self.readonly = readonly
        self._ib: Any | None = None
        self._lock = asyncio.Lock()

    async def connect(self, timeout: float = 5.0) -> None:
        """Open the IB connection. Idempotent (no reconnect when already OK)."""
        from ib_insync import IB

        async with self._lock:
            if self._ib is not None and self._ib.isConnected():
                return
            ib = IB()
            try:
                await asyncio.wait_for(
                    ib.connectAsync(
                        self.host,
                        self.port,
                        clientId=self.client_id,
                        readonly=self.readonly,
                    ),
                    timeout=timeout,
                )
            except (TimeoutError, OSError) as e:
                logger.warning("ib_connect_failed: %s", e)
                self._ib = None
                return
            self._ib = ib
            # Explicitly subscribe to the positions feed so ``ib.positions()``
            # populates after a reconnect (some gateways don't auto-push it).
            # Best-effort : the portfolio fallback in list_positions covers a miss.
            try:
                await asyncio.wait_for(ib.reqPositionsAsync(), timeout=timeout)
            except Exception as e:
                logger.warning("ib_reqpositions_failed: %s", e)
            logger.info("ib_connected host=%s port=%s clientId=%s", self.host, self.port, self.client_id)

    async def disconnect(self) -> None:
        async with self._lock:
            if self._ib is not None and self._ib.isConnected():
                self._ib.disconnect()
            self._ib = None

    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    def account_is_reporting(self) -> bool:
        """True when IB is actively streaming this account's data (account values
        present) — so an EMPTY position list means the account is genuinely FLAT,
        not a dead/transient feed. Reconciliation uses this to safely distinguish
        "close the stale book" from "don't touch it, IB is unreachable"."""
        if not self.is_connected():
            return False
        try:
            return bool(self._ib.accountValues()) or bool(self._ib.portfolio())
        except Exception:
            return False

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

    # Tags kept in `by_currency` — a personal-project-scale selection :
    # cash + valuation + P&L. The ~50 others (Billable, FundValue,
    # MutualFundValue, IndianStockHaircut, ColumnPrio, etc.) are IB noise
    # for our use case and are dropped.
    _CURRENCY_TAGS_KEEP: tuple[str, ...] = (
        "CashBalance",
        "NetLiquidationByCurrency",
        "UnrealizedPnL",
        "RealizedPnL",
        "FuturesPNL",
        "ExchangeRate",
    )

    async def account_summary(self) -> dict[str, Any]:
        """Return every numeric IB account tag, aggregated per tag.

        Many tags are only exposed in the account's base currency (often
        EUR or USD depending on the account) — so we take the available
        value in this order :
          1. BASE (= the account's native currency, clean aggregate)
          2. USD
          3. any other currency
        `by_currency` returns a summary (6 key tags) per real currency,
        without BASE (= global aggregate, redundant with the top-level columns).
        """
        ib = self._ensure()
        # Index values by tag then by currency, so the BASE > USD > others
        # priority can be applied at pick time.
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

        # DIAGNOSTIC (temporary): the per-currency cash split (USD/EUR) isn't
        # reaching the holdings widget in prod while the blended TotalCashValue
        # is. Log exactly what IB exposes — currencies seen + CashBalance per
        # currency — so the real fix targets the actual gap. Remove once fixed.
        logger.info(
            "account_summary_raw currencies=%s cashbalance=%s totalcash=%s",
            sorted(by_cur.keys()),
            {c: vs.get("CashBalance") for c, vs in by_cur.items() if "CashBalance" in vs},
            by_tag.get("TotalCashValue"),
        )

        # Flatten by_tag into out[tag] following the priority.
        out: dict[str, Any] = {"account": account}
        for tag, cur_to_val in by_tag.items():
            for preferred in ("BASE", "USD"):
                if preferred in cur_to_val:
                    out[tag] = cur_to_val[preferred]
                    break
            else:
                out[tag] = next(iter(cur_to_val.values()))

        # by_currency : for each real currency (≠ BASE), filter to the
        # relevant tags and drop currencies with no kept tag.
        out["by_currency"] = {}
        for cur, vs in by_cur.items():
            if cur == "BASE":
                continue
            kept = {tag: vs[tag] for tag in self._CURRENCY_TAGS_KEEP if tag in vs}
            if kept:
                out["by_currency"][cur] = kept
        return out

    async def list_positions(self) -> list[dict[str, Any]]:
        """Return live positions from IB (= broker truth, not our DB cache).

        Primary source is ``ib.positions()`` (reqPositions). That subscription can
        come back EMPTY right after a reconnect even while the per-account portfolio
        feed (reqAccountUpdates) is live and holding the same positions — so we fall
        back to ``ib.portfolio()`` to avoid a phantom "flat account". PortfolioItem
        uses ``averageCost`` where Position uses ``avgCost`` — read both.
        """
        ib = self._ensure()
        raw = list(ib.positions())
        if not raw:
            raw = [p for p in ib.portfolio() if abs(float(getattr(p, "position", 0) or 0)) > 0]
        out = []
        for p in raw:
            c = p.contract
            avg = getattr(p, "avgCost", None)
            if avg is None:
                avg = getattr(p, "averageCost", 0.0)
            out.append({
                "account": getattr(p, "account", None),
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
                "avg_cost": float(avg or 0.0),
            })
        return out

    # ---- Mutation operations ------------------------------------------------

    async def place_order(self, req: OrderRequest) -> dict[str, Any]:
        from ib_insync import Contract, LimitOrder, MarketOrder

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

        # limit_price=None ⇒ MarketOrder (spot CASH default — fill at touch,
        # the panel is for cash management, not price improvement).
        order = (
            LimitOrder(req.side, req.qty, req.limit_price)
            if req.limit_price
            else MarketOrder(req.side, req.qty)
        )
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
        if getattr(contract, "secType", "") == "FOP":
            # Options : never a plain market order (IB's option price-cap makes it
            # dribble / hang, exactly like opening SELL legs). Price off the LIVE
            # quote (BUY->ask, SELL->bid, a tick through) so it reaches the actual
            # touch even on a wide spread — this is what fills easiest. Only fall
            # back to the API's mark-based limit when IB returns no quote (common on
            # paper), and to a market order only if we have neither.
            lp = await _marketable_close_price(ib, contract, side)
            if lp is None:
                lp = limit_price
            order = LimitOrder(side, close_qty, lp) if lp else MarketOrder(side, close_qty)
        elif limit_price is not None:
            order = LimitOrder(side, close_qty, limit_price)  # future outside RTH
        else:
            order = MarketOrder(side, close_qty)               # future in RTH
        return ib.placeOrder(contract, order)


def trade_to_dict(trade: Any) -> dict[str, Any]:
    """Serialize an ib_insync Trade into a JSON-safe dict."""
    o = trade.order
    c = trade.contract
    s = trade.orderStatus
    # MarketOrder leaves lmtPrice at IB's UNSET_DOUBLE sentinel (float max) —
    # serialize that as "no limit", not a real price.
    lmt = float(o.lmtPrice) if o.lmtPrice else None
    if lmt is not None and lmt >= 1.7e308:
        lmt = None
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
        "limit_price": lmt,
        "status": s.status,
        "filled": float(s.filled),
        "remaining": float(s.remaining),
        "avg_fill_price": float(s.avgFillPrice) if s.avgFillPrice else None,
        # Idempotency key stamped by live_submit (``fxvol:{structure}:{order}``)
        # — the reaper's adoption sweep matches ghosts on it (EXEC-2).
        "order_ref": getattr(o, "orderRef", None) or None,
    }
