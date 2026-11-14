"""A simulated broker for the order-lifecycle tests (#496).

``tests/fixtures/ib.py`` records calls and never fills: every unit test of
the order path fakes ONE event. Nothing drove an order through a partial
fill, a cancel, the end of the day, a restart and a reconnect, which is
where the audit of 2026-11-13 found the book leaving the broker.

``SimBroker`` stands where ``ib_async.IB`` stands, behind the REAL
``OrderExecutor``. It speaks the subset of the library the engine uses and
hands back real ``ib_async`` objects (``Trade``, ``OrderStatus``, ``Fill``,
``Execution``), so the engine's own handlers run on its events.

Two sides:

* the engine's side is the ib_async surface: ``placeOrder``, ``cancelOrder``,
  ``openTrades``, ``trades``, ``positions``, ``reqExecutionsAsync``,
  ``qualifyContractsAsync``, ``reqTickersAsync``, ``accountValues``;
* the broker's side is what a test scripts: ``ack``, ``fill``, ``reject``,
  ``hold``, ``confirm_cancel``, ``end_of_day``, ``drop_connection``,
  ``restart_engine_view``, ``fail_next_place`` and ``fail_place_number``.

The broker keeps its own truth: every execution it ever made
(``executions``) and the position they add up to (``position``). A scenario
ends by comparing the book with it (``_assert_book_equals_broker`` in
``tests/unit/engines/execution/test_order_lifecycle_sim.py``).

Fault injection: ``silent=True`` on a broker-side call changes the broker's
state WITHOUT telling the engine, which is what a missed event, a crash
between two writes or a disconnection looks like from the engine.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from ib_async import CommissionReport, Contract, Execution, Fill, OrderStatus, Trade, TradeLogEntry

ACTIVE = ("PendingSubmit", "PreSubmitted", "Submitted", "PendingCancel", "Inactive")


class SimBroker:
    def __init__(self) -> None:
        self.connected = True
        self.reporting = True
        self._trades: list[Trade] = []
        self._hidden: set[int] = set()          # order ids a new session does not list
        self.executions: list[Fill] = []
        self.cancel_requests: list[int] = []
        self.fail_next_place: str | None = None
        self.fail_place_number: int | None = None   # the Nth placeOrder raises (1 = the first)
        self._place_calls = 0
        self.quote = {"bid": 0.0041, "ask": 0.0043}
        self._next_order_id = 1000
        self._next_exec = 0
        self._con_ids: dict[str, int] = {}

    # ── the engine's side: the ib_async surface ─────────────────────────────

    def isConnected(self) -> bool:
        return self.connected

    def disconnect(self) -> None:
        self.connected = False

    def accountValues(self) -> list[Any]:
        return [SimpleNamespace(tag="NetLiquidation", value="1000000", currency="USD")] if self.reporting else []

    def portfolio(self) -> list[Any]:
        return []

    def reqMarketDataType(self, _kind: int) -> None:
        return None

    async def qualifyContractsAsync(self, contract: Any) -> list[Any]:
        """IB resolves the contract: an id, a local symbol, a class."""
        if not getattr(contract, "localSymbol", ""):
            if getattr(contract, "secType", "") == "FUT":
                contract.localSymbol = "6EH7"
            else:
                right = getattr(contract, "right", "C") or "C"
                strike = float(getattr(contract, "strike", 0.0) or 0.0)
                contract.localSymbol = f"EUUG7 {right}{round(strike * 1000):04d}"
        contract.conId = self._con_ids.setdefault(contract.localSymbol, 9000 + len(self._con_ids))
        if not getattr(contract, "multiplier", ""):
            contract.multiplier = "125000"
        return [contract]

    async def reqTickersAsync(self, _contract: Any) -> list[Any]:
        bid, ask = self.quote["bid"], self.quote["ask"]
        return [SimpleNamespace(bid=bid, ask=ask, marketPrice=lambda: (bid + ask) / 2)]

    def placeOrder(self, contract: Any, order: Any) -> Trade:
        if not self.connected:
            raise ConnectionError("not connected")
        self._place_calls += 1
        if self.fail_place_number is not None and self._place_calls == self.fail_place_number:
            raise RuntimeError("order refused by the broker")
        if self.fail_next_place is not None:
            reason, self.fail_next_place = self.fail_next_place, None
            raise RuntimeError(reason)
        self._next_order_id += 1
        order.orderId = self._next_order_id
        order.permId = 500_000 + self._next_order_id
        trade = Trade(
            contract=contract, order=order,
            orderStatus=OrderStatus(orderId=order.orderId, status="PreSubmitted", filled=0.0,
                                    remaining=float(order.totalQuantity), permId=order.permId),
            fills=[], log=[TradeLogEntry(time=datetime.now(UTC), status="PreSubmitted")],
        )
        self._trades.append(trade)
        return trade

    def cancelOrder(self, order: Any) -> None:
        """The engine ASKS. The order is cancelled when the broker says so
        (``confirm_cancel``), and it can still fill in between."""
        self.cancel_requests.append(int(order.orderId))
        trade = self._find(int(order.orderId))
        if trade.orderStatus.status in ACTIVE:
            self._status(trade, "PendingCancel")

    def reqGlobalCancel(self) -> None:
        for t in self.openTrades():
            self.cancelOrder(t.order)

    def openTrades(self) -> list[Trade]:
        return [t for t in self._visible() if t.orderStatus.status in ACTIVE]

    def trades(self) -> list[Trade]:
        return list(self._visible())

    def positions(self) -> list[Any]:
        out = []
        for symbol in sorted({f.contract.localSymbol for f in self.executions}):
            qty = self.position(symbol)
            if qty:
                contract = next(f.contract for f in self.executions if f.contract.localSymbol == symbol)
                out.append(SimpleNamespace(contract=contract, position=float(qty), avgCost=0.0, account="DU1"))
        return out

    async def reqExecutionsAsync(self) -> list[Fill]:
        """The day's executions: what a restarted or reconnected engine asks for."""
        return list(self.executions)

    # ── the broker's side: what a test scripts ──────────────────────────────

    def ack(self, order_id: int) -> None:
        self._status(self._find(order_id), "Submitted")

    def fill(self, order_id: int, qty: int | None = None, price: float | None = None, *, silent: bool = False) -> Fill:
        """Execute ``qty`` (default: all that remains). ``silent`` = the engine
        is not told: the execution exists at the broker only."""
        trade = self._find(order_id)
        if trade.orderStatus.status not in ACTIVE:
            raise AssertionError(f"order {order_id} is {trade.orderStatus.status}: it cannot fill")
        remaining = int(trade.orderStatus.remaining)
        qty = remaining if qty is None else int(qty)
        if qty <= 0 or qty > remaining:
            raise AssertionError(f"order {order_id}: cannot fill {qty} of {remaining} remaining")
        self._next_exec += 1
        price = float(price if price is not None else self.quote["ask"])
        now = datetime.now(UTC)
        execution = Execution(
            execId=f"sim.{order_id}.{self._next_exec:04d}", time=now, exchange="CME",
            side="BOT" if trade.order.action == "BUY" else "SLD", shares=float(qty), price=price,
            permId=int(trade.order.permId), orderId=order_id, orderRef=trade.order.orderRef or "",
            cumQty=float(trade.orderStatus.filled) + qty,
        )
        fill = Fill(contract=trade.contract, execution=execution,
                    commissionReport=CommissionReport(), time=now)   # the report comes later, empty here
        self.executions.append(fill)
        trade.fills.append(fill)
        trade.orderStatus.filled = float(trade.orderStatus.filled) + qty
        trade.orderStatus.remaining = float(remaining - qty)
        done = remaining - qty == 0
        if done:
            trade.orderStatus.status = "Filled"
            trade.log.append(TradeLogEntry(time=now, status="Filled"))
        if not silent:
            trade.fillEvent.emit(trade, fill)
            if done:
                trade.statusEvent.emit(trade)
        return fill

    def reject(self, order_id: int, message: str = "Order rejected - reason: margin", code: int = 201,
               *, silent: bool = False) -> None:
        trade = self._find(order_id)
        trade.log.append(TradeLogEntry(time=datetime.now(UTC), status="Cancelled", message=message, errorCode=code))
        self._status(trade, "Cancelled", silent=silent, log=False)

    def hold(self, order_id: int, why: str = "outside regular trading hours") -> None:
        """IB keeps the order without working it: Inactive, no error."""
        trade = self._find(order_id)
        trade.orderStatus.whyHeld = why
        self._status(trade, "Inactive")

    def confirm_cancel(self, order_id: int, *, silent: bool = False) -> None:
        self._status(self._find(order_id), "Cancelled", silent=silent)

    def end_of_day(self, *, silent: bool = False) -> list[int]:
        """Every DAY order still working is ended by the broker: Cancelled,
        with no error and no request from the desk."""
        ended = []
        for t in list(self.openTrades()):
            if getattr(t.order, "tif", "DAY") == "DAY":
                self._status(t, "Cancelled", silent=silent)
                ended.append(int(t.order.orderId))
        return ended

    def drop_connection(self) -> None:
        self.connected = False

    def restore_connection(self) -> None:
        self.connected = True

    def restart_engine_view(self) -> None:
        """A new API session: the trades of the old one carry no handler of
        the new engine, and an order that ended is no longer listed."""
        for t in self._trades:
            if t.orderStatus.status not in ACTIVE:
                self._hidden.add(int(t.order.orderId))
            for name in ("statusEvent", "fillEvent", "commissionReportEvent"):
                getattr(t, name).clear()

    # ── the broker's truth ──────────────────────────────────────────────────

    def position(self, local_symbol: str) -> int:
        return int(sum(
            (1 if f.execution.side == "BOT" else -1) * f.execution.shares
            for f in self.executions if f.contract.localSymbol == local_symbol
        ))

    def order_by_ref(self, order_ref: str) -> Trade | None:
        return next((t for t in self._trades if (t.order.orderRef or "") == order_ref), None)

    def status(self, order_id: int) -> str:
        return str(self._find(order_id).orderStatus.status)

    def ids(self) -> list[int]:
        return [int(t.order.orderId) for t in self._trades]

    def is_working(self, order_id: int) -> bool:
        return self.status(order_id) in ACTIVE

    # ── internals ───────────────────────────────────────────────────────────

    def _visible(self) -> list[Trade]:
        return [t for t in self._trades if int(t.order.orderId) not in self._hidden]

    def _find(self, order_id: int) -> Trade:
        for t in self._trades:
            if int(t.order.orderId) == int(order_id):
                return t
        raise KeyError(f"no order {order_id} at the broker")

    def _status(self, trade: Trade, status: str, *, silent: bool = False, log: bool = True) -> None:
        trade.orderStatus.status = status
        if log:
            trade.log.append(TradeLogEntry(time=datetime.now(UTC), status=status))
        if not silent:
            trade.statusEvent.emit(trade)


def option(local_symbol: str = "EUUG7 C1110", *, con_id: int = 9001, strike: float = 1.11, right: str = "C") -> Contract:
    return Contract(secType="FOP", symbol="EUR", exchange="CME", currency="USD", localSymbol=local_symbol,
                    conId=con_id, strike=strike, right=right, multiplier="125000",
                    lastTradeDateOrContractMonth="20270205", tradingClass="EUU")
