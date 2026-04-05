from __future__ import annotations

from threading import RLock
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from ib_insync import Forex, LimitOrder, MarketOrder

from services.ib_client import IBClient


class OrderWorker(QObject):
    enqueue_order = pyqtSignal(object)
    enqueue_preview = pyqtSignal(object)
    enqueue_cancel_all = pyqtSignal(object)
    order_result = pyqtSignal(object)
    failed = pyqtSignal(str)

    # Wire worker signals and initialize execution state.
    def __init__(self, ib_client: IBClient, io_lock: RLock) -> None:
        super().__init__()
        self.ib_client = ib_client
        self.io_lock = io_lock
        self._running = False
        self.enqueue_order.connect(self.place_order)
        self.enqueue_preview.connect(self.preview_order)
        self.enqueue_cancel_all.connect(self.cancel_all_orders)

    @pyqtSlot()
    # Mark the worker as ready to process queued requests.
    def start(self) -> None:
        self._running = True

    @pyqtSlot()
    # Mark the worker as stopped and reject new requests.
    def stop(self) -> None:
        self._running = False

    @staticmethod
    # Normalize a symbol string to IB-friendly uppercase format.
    def _normalize_symbol(raw_symbol: str) -> str:
        return str(raw_symbol).strip().upper().replace("/", "")

    @staticmethod
    # Parse and sanitize an order request payload.
    def _normalize_request(request: Any) -> dict[str, Any] | None:
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
        raw_take_profit = request.get("take_profit", None)
        raw_stop_loss = request.get("stop_loss", None)
        try:
            take_profit = float(raw_take_profit) if raw_take_profit is not None else 0.0
        except (TypeError, ValueError):
            take_profit = 0.0
        try:
            stop_loss = float(raw_stop_loss) if raw_stop_loss is not None else 0.0
        except (TypeError, ValueError):
            stop_loss = 0.0

        return {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": quantity,
            "limit_price": limit_price,
            "take_profit": take_profit if take_profit > 0 else None,
            "stop_loss": stop_loss if stop_loss > 0 else None,
        }

    @staticmethod
    # Validate normalized order fields and return an error message when invalid.
    def _validate_request(normalized: dict[str, Any]) -> str | None:
        symbol = normalized["symbol"]
        side = normalized["side"]
        order_type = normalized["order_type"]
        quantity = normalized["quantity"]
        limit_price = normalized["limit_price"]
        take_profit = normalized["take_profit"]
        stop_loss = normalized["stop_loss"]

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
        has_take_profit = take_profit is not None
        has_stop_loss = stop_loss is not None
        if has_take_profit != has_stop_loss:
            return "Set both TP and SL, or leave both empty."
        if has_take_profit and order_type != "LMT":
            return "TP/SL is currently supported only for LMT orders."
        if has_take_profit and has_stop_loss:
            if side == "BUY":
                if take_profit <= limit_price:
                    return "For BUY orders, TP must be above the limit price."
                if stop_loss >= limit_price:
                    return "For BUY orders, SL must be below the limit price."
            else:
                if take_profit >= limit_price:
                    return "For SELL orders, TP must be below the limit price."
                if stop_loss <= limit_price:
                    return "For SELL orders, SL must be above the limit price."
        return None

    @staticmethod
    # Build an IB market or limit order object.
    def _build_order(side: str, order_type: str, quantity: int, limit_price: float) -> Any:
        if order_type == "MKT":
            return MarketOrder(side, quantity)
        return LimitOrder(side, quantity, limit_price)

    @pyqtSlot(object)
    # Execute a validated order request through the IB client.
    def place_order(self, request: Any) -> None:
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
        take_profit = normalized["take_profit"]
        stop_loss = normalized["stop_loss"]
        has_bracket = take_profit is not None and stop_loss is not None

        try:
            with self.io_lock:
                if not self.ib_client.is_connected():
                    self.order_result.emit({"ok": False, "kind": "order", "message": "Not connected to IBKR."})
                    return

                contract = Forex(symbol)
                qualified_contract = self.ib_client.qualify_contract(contract) or contract
                if has_bracket:
                    self.ib_client.clear_last_error()
                    bracket_orders = self.ib_client.build_bracket_orders(
                        side=side,
                        quantity=quantity,
                        limit_price=limit_price,
                        take_profit_price=take_profit,
                        stop_loss_price=stop_loss,
                    )
                    if not bracket_orders:
                        reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                        self.order_result.emit(
                            {
                                "ok": False,
                                "kind": "order",
                                "message": (
                                    f"Bracket build failed ({side} {quantity} {symbol} {order_type}, "
                                    f"TP={take_profit}, SL={stop_loss}) - {reason}"
                                ),
                            }
                        )
                        return

                    for bracket_order in bracket_orders:
                        self.ib_client.clear_last_error()
                        trade = self.ib_client.place_order(qualified_contract, bracket_order)
                        if trade is None:
                            reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                            self.order_result.emit(
                                {
                                    "ok": False,
                                    "kind": "order",
                                    "message": (
                                        f"Bracket order rejected by API ({side} {quantity} {symbol} {order_type}, "
                                        f"TP={take_profit}, SL={stop_loss}) - {reason}"
                                    ),
                                }
                            )
                            return
                else:
                    self.ib_client.clear_last_error()
                    order = self._build_order(side, order_type, quantity, limit_price)
                    trade = self.ib_client.place_order(qualified_contract, order)
                    if trade is None:
                        reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                        self.order_result.emit(
                            {
                                "ok": False,
                                "kind": "order",
                                "message": f"Order rejected by API ({side} {quantity} {symbol} {order_type}) - {reason}",
                            }
                        )
                        return

            self.order_result.emit(
                {
                    "ok": True,
                    "kind": "order",
                    "message": (
                        f"Order sent: {side} {quantity} {symbol} {order_type}."
                        if not has_bracket
                        else (
                            f"Bracket sent: {side} {quantity} {symbol} {order_type} "
                            f"@ {limit_price} TP={take_profit} SL={stop_loss}."
                        )
                    ),
                    "symbol": symbol,
                    "side": side,
                    "order_type": order_type,
                    "quantity": quantity,
                    "limit_price": limit_price,
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    @pyqtSlot(object)
    # Run a what-if preview for a validated order request.
    def preview_order(self, request: Any) -> None:
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
        take_profit = normalized["take_profit"]
        stop_loss = normalized["stop_loss"]
        has_bracket = take_profit is not None and stop_loss is not None

        try:
            with self.io_lock:
                if not self.ib_client.is_connected():
                    self.order_result.emit({"ok": False, "kind": "preview", "message": "Not connected to IBKR."})
                    return

                contract = Forex(symbol)
                qualified_contract = self.ib_client.qualify_contract(contract) or contract
                self.ib_client.clear_last_error()
                order = self._build_order(side, order_type, quantity, limit_price)
                what_if = self.ib_client.what_if_order(qualified_contract, order)
                if what_if is None:
                    reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                    self.order_result.emit(
                        {
                            "ok": False,
                            "kind": "preview",
                            "message": f"Preview failed for {side} {quantity} {symbol} {order_type} - {reason}",
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
            if has_bracket:
                preview_message += f" | TP={take_profit} SL={stop_loss}"
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
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))

    @pyqtSlot(object)
    # Cancel all currently open orders through the IB client.
    def cancel_all_orders(self, _request: Any = None) -> None:
        if not self._running:
            self.order_result.emit({"ok": False, "kind": "cancel_all", "message": "Order worker is stopped."})
            return
        try:
            with self.io_lock:
                if not self.ib_client.is_connected():
                    self.order_result.emit({"ok": False, "kind": "cancel_all", "message": "Not connected to IBKR."})
                    return
                ok, cancelled_count, message = self.ib_client.cancel_all_open_orders()

            self.order_result.emit(
                {
                    "ok": bool(ok),
                    "kind": "cancel_all",
                    "message": str(message),
                    "cancelled_count": int(cancelled_count),
                }
            )
        except Exception as exc:
            self.failed.emit(str(exc))
