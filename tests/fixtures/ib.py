"""ib_async object factories for unit tests (#338, audit 07#10).

Realistic shapes, including the two details that bit production:

- ``fake_fill`` carries an EMPTY ``commissionReport`` by default, because that
  is what ib_async hands to ``fillEvent``; the real report arrives later on
  ``commissionReportEvent`` (EX-03, #322). Pass ``commission=`` to model the
  report having already landed.
- ``fake_trade`` carries a ``log`` list of ``TradeLogEntry``-shaped entries;
  ``error=`` appends an IB error entry (``errorCode`` set), which is how a
  reject is told apart from a hold (#320, #376).
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any


class FakeEvent:
    """Stand-in for ``eventkit.Event``: collects handlers, ``emit`` calls them."""

    def __init__(self) -> None:
        self.handlers: list[Any] = []

    def __iadd__(self, fn: Any) -> FakeEvent:
        self.handlers.append(fn)
        return self

    def emit(self, *args: Any) -> None:
        for fn in list(self.handlers):
            fn(*args)


def fake_contract(
    *, local_symbol: str = "EUUZ6 C1130", con_id: int = 853091540, sec_type: str = "FOP",
    currency: str = "USD", exchange: str = "CME", symbol: str = "EUR",
) -> SimpleNamespace:
    return SimpleNamespace(localSymbol=local_symbol, conId=con_id, secType=sec_type,
                           currency=currency, exchange=exchange, symbol=symbol)


def fake_trade(
    *, status: str = "Submitted", order_id: int = 7, perm_id: int | None = 422816514,
    lmt: float | None = 0.0123, action: str = "BUY", qty: int = 10,
    error: str | None = None, error_code: int = 201, why_held: str | None = None,
    order_ref: str | None = None, contract: SimpleNamespace | None = None,
    fills: list[Any] | None = None,
) -> SimpleNamespace:
    """A ``Trade`` as the handlers see it: order, contract, orderStatus, log,
    fills, plus the three events (``statusEvent``, ``fillEvent``,
    ``commissionReportEvent``) so ``attach_*`` can wire them."""
    log = [SimpleNamespace(time=datetime.now(UTC), status=status, message="", errorCode=0)]
    if error:
        log.append(SimpleNamespace(time=datetime.now(UTC), status=status, message=error, errorCode=error_code))
    order = SimpleNamespace(orderId=order_id, permId=perm_id, lmtPrice=lmt if lmt is not None else 1.7976931348623157e308,
                            action=action, totalQuantity=qty, orderRef=order_ref or "")
    return SimpleNamespace(
        order=order,
        contract=contract or fake_contract(),
        orderStatus=SimpleNamespace(status=status, whyHeld=why_held or "", filled=0, remaining=qty, avgFillPrice=0.0),
        log=log,
        fills=list(fills or []),
        statusEvent=FakeEvent(), fillEvent=FakeEvent(), commissionReportEvent=FakeEvent(),
    )


def fake_fill(
    *, exec_id: str = "0000e1a7.6a2b1c3d.01.01", qty: int = 10, price: float = 0.0123,
    side: str = "BOT", commission: float | None = None, currency: str = "USD",
    contract: SimpleNamespace | None = None, time: datetime | None = None,
) -> SimpleNamespace:
    """A ``Fill``. ``commission=None`` = the realistic empty report at fill time."""
    report = (SimpleNamespace(execId=exec_id, commission=0.0, currency="", realizedPNL=0.0)
              if commission is None
              else SimpleNamespace(execId=exec_id, commission=commission, currency=currency, realizedPNL=0.0))
    return SimpleNamespace(
        execution=SimpleNamespace(execId=exec_id, shares=qty, price=price, side=side,
                                  time=time or datetime.now(UTC), exchange="CME", permId=422816514),
        commissionReport=report,
        contract=contract or fake_contract(currency=currency),
    )


def fake_commission_report(*, exec_id: str, commission: float = 2.37, currency: str = "USD") -> SimpleNamespace:
    return SimpleNamespace(execId=exec_id, commission=commission, currency=currency, realizedPNL=0.0, yield_=0.0)


class FakeIB:
    """Minimal ``IB``: records placed orders, qualifies any contract, cancels by id."""

    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.placed: list[tuple[Any, Any]] = []
        self.cancelled: list[int] = []
        self._next_order_id = 100
        self._open: list[Any] = []

    def isConnected(self) -> bool:
        return self.connected

    def placeOrder(self, contract: Any, order: Any) -> SimpleNamespace:
        self._next_order_id += 1
        self.placed.append((contract, order))
        t = fake_trade(status="PreSubmitted", order_id=self._next_order_id, perm_id=900 + self._next_order_id,
                       lmt=getattr(order, "lmtPrice", None), action=getattr(order, "action", "BUY"),
                       qty=int(getattr(order, "totalQuantity", 0) or 0), order_ref=getattr(order, "orderRef", None),
                       contract=contract if isinstance(contract, SimpleNamespace) else None)
        self._open.append(t)
        return t

    def cancelOrder(self, order: Any) -> None:
        self.cancelled.append(int(order.orderId))

    def openTrades(self) -> list[Any]:
        return list(self._open)

    async def qualifyContractsAsync(self, contract: Any) -> list[Any]:
        return [contract]
