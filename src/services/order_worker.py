from __future__ import annotations

from threading import RLock

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from ib_insync import Forex, LimitOrder, MarketOrder

from services.ib_client import IBClient


class OrderWorker(QObject):
    enqueue_order = pyqtSignal(object)
    enqueue_preview = pyqtSignal(object)
    order_result = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, ib_client: IBClient, io_lock: RLock):
        super().__init__()
        self.ib_client = ib_client
        self.io_lock = io_lock
        self._running = False
        self.enqueue_order.connect(self.place_order)
        self.enqueue_preview.connect(self.preview_order)

    @pyqtSlot()
    def start(self):
        self._running = True

    @pyqtSlot()
    def stop(self):
        self._running = False

    @staticmethod
    def _normalize_symbol(raw_symbol: str) -> str:
        return str(raw_symbol).strip().upper().replace("/", "")

    @staticmethod
    def _normalize_request(request: dict) -> dict | None:
        if not isinstance(request, dict):
            return None

        symbol = OrderWorker._normalize_symbol(request.get("symbol", ""))
        side = str(request.get("side", "")).strip().upper()
        order_type = str(request.get("order_type", "")).strip().upper()

        try:
            quantity = int(request.get("quantity", 0))
        except (TypeError, ValueError):
            quantity = 0
        try:
            limit_price = float(request.get("limit_price", 0.0))
        except (TypeError, ValueError):
            limit_price = 0.0

        return {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "limit_price": limit_price,
        }

    @staticmethod
    def _validate_request(normalized: dict) -> str | None:
        symbol = normalized["symbol"]
        side = normalized["side"]
        order_type = normalized["order_type"]
        quantity = normalized["quantity"]
        limit_price = normalized["limit_price"]

        if not symbol or len(symbol) < 6:
            return "Invalid symbol."
        if side not in {"BUY", "SELL"}:
            return "Invalid side."
        if order_type not in {"MKT", "LMT"}:
            return "Invalid order type."
        if quantity <= 0:
            return "Quantity must be > 0."
        if order_type == "LMT" and limit_price <= 0:
            return "Limit price must be > 0 for LMT orders."
        return None

    @staticmethod
    def _build_order(side: str, order_type: str, quantity: int, limit_price: float):
        if order_type == "MKT":
            return MarketOrder(side, quantity)
        return LimitOrder(side, quantity, limit_price)

    @pyqtSlot(object)
    def place_order(self, request):
        if not self._running:
            self.order_result.emit({"ok": False, "kind": "order", "message": "Order worker is stopped."})
            return
        normalized = self._normalize_request(request)
        if normalized is None:
            self.order_result.emit({"ok": False, "kind": "order", "message": "Invalid order payload."})
            return

        validation_error = self._validate_request(normalized)
        if validation_error is not None:
            self.order_result.emit({"ok": False, "kind": "order", "message": validation_error})
            return

        symbol = normalized["symbol"]
        side = normalized["side"]
        order_type = normalized["order_type"]
        quantity = normalized["quantity"]
        limit_price = normalized["limit_price"]

        try:
            with self.io_lock:
                if not self.ib_client.is_connected():
                    self.order_result.emit({"ok": False, "kind": "order", "message": "Not connected to IBKR."})
                    return

                contract = Forex(symbol)
                qualified_contract = self.ib_client.qualify_contract(contract) or contract
                order = self._build_order(side, order_type, quantity, limit_price)

                trade = self.ib_client.place_order(qualified_contract, order)
                if trade is None:
                    self.order_result.emit(
                        {
                            "ok": False,
                            "kind": "order",
                            "message": f"Order rejected by API ({side} {quantity} {symbol} {order_type}).",
                        }
                    )
                    return

            self.order_result.emit(
                {
                    "ok": True,
                    "kind": "order",
                    "message": f"Order sent: {side} {quantity} {symbol} {order_type}.",
                    "symbol": symbol,
                    "side": side,
                    "order_type": order_type,
                    "quantity": quantity,
                    "limit_price": limit_price,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    @pyqtSlot(object)
    def preview_order(self, request):
        if not self._running:
            self.order_result.emit({"ok": False, "kind": "preview", "message": "Order worker is stopped."})
            return
        normalized = self._normalize_request(request)
        if normalized is None:
            self.order_result.emit({"ok": False, "kind": "preview", "message": "Invalid order payload."})
            return

        validation_error = self._validate_request(normalized)
        if validation_error is not None:
            self.order_result.emit({"ok": False, "kind": "preview", "message": validation_error})
            return

        symbol = normalized["symbol"]
        side = normalized["side"]
        order_type = normalized["order_type"]
        quantity = normalized["quantity"]
        limit_price = normalized["limit_price"]

        try:
            with self.io_lock:
                if not self.ib_client.is_connected():
                    self.order_result.emit({"ok": False, "kind": "preview", "message": "Not connected to IBKR."})
                    return

                contract = Forex(symbol)
                qualified_contract = self.ib_client.qualify_contract(contract) or contract
                order = self._build_order(side, order_type, quantity, limit_price)
                what_if = self.ib_client.what_if_order(qualified_contract, order)
                if what_if is None:
                    self.order_result.emit(
                        {
                            "ok": False,
                            "kind": "preview",
                            "message": f"Preview failed for {side} {quantity} {symbol} {order_type}.",
                        }
                    )
                    return

            init_margin = getattr(what_if, "initMarginChange", "--")
            maint_margin = getattr(what_if, "maintMarginChange", "--")
            commission = getattr(what_if, "commission", "--")
            preview_message = (
                f"Preview {side} {quantity} {symbol} {order_type} | "
                f"InitMargin: {init_margin} MaintMargin: {maint_margin} Commission: {commission}"
            )
            self.order_result.emit(
                {
                    "ok": True,
                    "kind": "preview",
                    "message": preview_message,
                    "symbol": symbol,
                    "side": side,
                    "order_type": order_type,
                    "quantity": quantity,
                    "limit_price": limit_price,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))
