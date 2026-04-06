from __future__ import annotations

import logging
from threading import RLock
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot
from ib_insync import Forex, LimitOrder, MarketOrder

from services.ib_client import IBClient


logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(levelname)s][order_worker] %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.DEBUG)
logger.propagate = False


class OrderWorker(QObject):
    enqueue_order = pyqtSignal(object)
    enqueue_preview = pyqtSignal(object)
    order_result = pyqtSignal(object)
    failed = pyqtSignal(str)

    _REJECTED_STATUSES = {"APICANCELLED", "CANCELLED", "INACTIVE"}

    # Wire worker signals and initialize execution state.
    def __init__(self, ib_client: IBClient, io_lock: RLock) -> None:
        super().__init__()
        self.ib_client = ib_client
        self.io_lock = io_lock
        self._running = False
        self.enqueue_order.connect(self.place_order)
        self.enqueue_preview.connect(self.preview_order)

    @pyqtSlot()
    # Mark the worker as ready to process queued requests.
    def start(self) -> None:
        self._running = True
        logger.debug("OrderWorker started.")

    @pyqtSlot()
    # Mark the worker as stopped and reject new requests.
    def stop(self) -> None:
        self._running = False
        logger.debug("OrderWorker stopped.")

    @staticmethod
    # Normalize a symbol string to IB-friendly uppercase format.
    def _normalize_symbol(raw_symbol: str) -> str:
        return str(raw_symbol).strip().upper().replace("/", "")

    @staticmethod
    # Parse numeric field to float, returning default on conversion failure.
    def _parse_float(raw: Any, default: float = 0.0) -> float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    # Parse optional numeric field, returning None when missing/invalid/non-positive.
    def _parse_positive_optional(raw: Any) -> float | None:
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @staticmethod
    # Parse and sanitize an order request payload.
    def _normalize_request(request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return None

        symbol = OrderWorker._normalize_symbol(request.get("symbol", ""))
        side = str(request.get("side", "")).strip().upper()
        order_type = str(request.get("order_type", "")).strip().upper()

        try:
            volume = int(request.get("volume", request.get("quantity", 0)))
        except (TypeError, ValueError):
            volume = 0

        limit_price = OrderWorker._parse_float(request.get("limit_price", 0.0), default=0.0)
        reference_price = OrderWorker._parse_positive_optional(request.get("reference_price", None))
        use_bracket = bool(request.get("use_bracket", False))
        take_profit_pct = OrderWorker._parse_positive_optional(request.get("take_profit_pct", None))
        stop_loss_pct = OrderWorker._parse_positive_optional(request.get("stop_loss_pct", None))

        return {
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "volume": volume,
            "quantity": volume,
            "limit_price": limit_price,
            "reference_price": reference_price,
            "use_bracket": use_bracket,
            "take_profit_pct": take_profit_pct,
            "stop_loss_pct": stop_loss_pct,
        }

    @staticmethod
    # Validate normalized order fields and return an error message when invalid.
    def _validate_request(normalized: dict[str, Any]) -> str | None:
        symbol = normalized["symbol"]
        side = normalized["side"]
        order_type = normalized["order_type"]
        quantity = normalized["quantity"]
        limit_price = normalized["limit_price"]
        use_bracket = normalized["use_bracket"]
        tp_pct = normalized["take_profit_pct"]
        sl_pct = normalized["stop_loss_pct"]

        if not symbol or len(symbol) < 6:
            return "Invalid symbol."
        if side not in {"BUY", "SELL"}:
            return "Invalid side."
        if order_type not in {"MKT", "LMT"}:
            return "Invalid order type."
        if quantity <= 0:
            return "Volume must be > 0."
        if order_type == "LMT" and limit_price <= 0:
            return "Limit price must be > 0 for LMT orders."
        if use_bracket and (tp_pct is None or sl_pct is None):
            return "Set both TP% and SL% when bracket is enabled."
        return None

    @staticmethod
    # Build an IB market or limit order object.
    def _build_order(side: str, order_type: str, quantity: int, limit_price: float) -> Any:
        if order_type == "MKT":
            order = MarketOrder(side, quantity)
            order.tif = "GTC"
            return order
        order = LimitOrder(side, quantity, limit_price)
        order.tif = "DAY"
        return order

    @staticmethod
    # Resolve the entry price used to derive TP/SL bracket levels.
    def _resolve_entry_price(normalized: dict[str, Any]) -> float | None:
        order_type = normalized["order_type"]
        if order_type == "LMT":
            return float(normalized["limit_price"])
        return normalized["reference_price"]

    @staticmethod
    # Convert TP/SL percentages into absolute prices from the entry price.
    def _derive_bracket_prices(side: str, entry_price: float, tp_pct: float, sl_pct: float) -> tuple[float, float]:
        tp_factor = float(tp_pct) / 100.0
        sl_factor = float(sl_pct) / 100.0
        if side == "BUY":
            take_profit = float(entry_price) * (1.0 + tp_factor)
            stop_loss = float(entry_price) * (1.0 - sl_factor)
        else:
            take_profit = float(entry_price) * (1.0 - tp_factor)
            stop_loss = float(entry_price) * (1.0 + sl_factor)
        if take_profit <= 0 or stop_loss <= 0:
            raise ValueError("Derived TP/SL prices must be positive.")
        return take_profit, stop_loss

    @staticmethod
    # Extract compact trade metadata returned by IB Gateway.
    def _trade_debug_payload(trade: Any) -> dict[str, Any]:
        order = getattr(trade, "order", None)
        order_status = getattr(trade, "orderStatus", None)
        contract = getattr(trade, "contract", None)
        return {
            "orderId": getattr(order, "orderId", None),
            "permId": getattr(order_status, "permId", getattr(order, "permId", None)),
            "clientId": getattr(order, "clientId", None),
            "status": getattr(order_status, "status", None),
            "filled": getattr(order_status, "filled", None),
            "remaining": getattr(order_status, "remaining", None),
            "avgFillPrice": getattr(order_status, "avgFillPrice", None),
            "symbol": getattr(contract, "symbol", None),
            "secType": getattr(contract, "secType", None),
        }

    @staticmethod
    # Return normalized IB order status text from a trade object.
    def _trade_status(trade: Any) -> str:
        order_status = getattr(trade, "orderStatus", None)
        status = str(getattr(order_status, "status", "")).strip().upper()
        return status

    @staticmethod
    # Extract what-if fields returned by IB Gateway.
    def _what_if_debug_payload(what_if: Any) -> dict[str, Any]:
        keys = (
            "initMarginBefore",
            "initMarginChange",
            "initMarginAfter",
            "maintMarginBefore",
            "maintMarginChange",
            "maintMarginAfter",
            "equityWithLoanBefore",
            "equityWithLoanChange",
            "equityWithLoanAfter",
            "commission",
            "minCommission",
            "maxCommission",
            "warningText",
        )
        return {key: getattr(what_if, key, "--") for key in keys}

    # Submit one order and return (ok, reason).
    def _submit_one(self, contract: Any, order: Any) -> tuple[bool, str]:
        logger.debug("ib.placeOrder() request contract=%r order=%r", contract, order)
        self.ib_client.clear_last_error()
        logger.debug("self.ib_client.clear_last_error() before ib.placeOrder()")
        trade = self.ib_client.place_order(contract, order)
        if trade is None:
            logger.debug("ib.placeOrder() failure reason=%s", self.ib_client.get_last_error_text())
            logger.debug("ib.placeOrder() returned None")
            return False, self.ib_client.get_last_error_text() or "Unknown IB error."

        trade_payload = self._trade_debug_payload(trade)
        trade_status = self._trade_status(trade)
        logger.debug("order submission accepted ib_response=%r", trade_payload)
        if trade_status in self._REJECTED_STATUSES:
            return False, self.ib_client.get_last_error_text() or f"IB status={trade_status}"
        return True, ""

    @pyqtSlot(object)
    # Execute a validated order request through the IB client.
    def place_order(self, request: Any) -> None:
        logger.debug("place_order received payload=%r", request)
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
        use_bracket = normalized["use_bracket"]
        tp_pct = normalized["take_profit_pct"]
        sl_pct = normalized["stop_loss_pct"]

        try:
            with self.io_lock:
                if not self.ib_client.is_connected():
                    self.order_result.emit({"ok": False, "kind": "order", "message": "Not connected to IBKR."})
                    return

                contract = Forex(symbol)
                logger.debug("Forex() built contract=%r", contract)
                qualified_contract = contract
                logger.debug("order_worker using direct Forex contract without qualification.")

                take_profit = None
                stop_loss = None
                bracket_entry_price = None
                if use_bracket:
                    entry_price = self._resolve_entry_price(normalized)
                    if entry_price is None or entry_price <= 0:
                        self.order_result.emit(
                            {
                                "ok": False,
                                "kind": "order",
                                "message": "Cannot derive bracket levels: invalid entry price.",
                            }
                        )
                        return

                    try:
                        take_profit, stop_loss = self._derive_bracket_prices(
                            side=side,
                            entry_price=entry_price,
                            tp_pct=float(tp_pct),
                            sl_pct=float(sl_pct),
                        )
                    except Exception as exc:
                        self.order_result.emit(
                            {
                                "ok": False,
                                "kind": "order",
                                "message": f"Cannot derive bracket levels - {exc}",
                            }
                        )
                        return

                    bracket_entry_price = float(entry_price)
                    self.ib_client.clear_last_error()
                    logger.debug("self.ib_client.clear_last_error() before build_bracket_orders()")
                    bracket_orders = self.ib_client.build_bracket_orders(
                        side=side,
                        quantity=quantity,
                        limit_price=bracket_entry_price,
                        take_profit_price=take_profit,
                        stop_loss_price=stop_loss,
                        parent_order_type=order_type,
                    )
                    logger.debug("order_worker build_bracket_orders() response=%r", bracket_orders)
                    if not bracket_orders:
                        reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                        self.order_result.emit(
                            {
                                "ok": False,
                                "kind": "order",
                                "message": (
                                    f"Bracket build failed ({side} {quantity} {symbol} {order_type}) - {reason}"
                                ),
                            }
                        )
                        return

                    for bracket_order in bracket_orders:
                        logger.debug("order_worker bracket order built=%r", bracket_order)
                        ok, reason = self._submit_one(qualified_contract, bracket_order)
                        if not ok:
                            self.order_result.emit(
                                {
                                    "ok": False,
                                    "kind": "order",
                                    "message": (
                                        f"Bracket order rejected ({side} {quantity} {symbol} {order_type}) - {reason}"
                                    ),
                                }
                            )
                            return
                else:
                    order = self._build_order(side, order_type, quantity, limit_price)
                    logger.debug("order_worker single order built=%r", order)
                    ok, reason = self._submit_one(qualified_contract, order)
                    if not ok:
                        self.order_result.emit(
                            {
                                "ok": False,
                                "kind": "order",
                                "message": f"Order rejected ({side} {quantity} {symbol} {order_type}) - {reason}",
                            }
                        )
                        return

            if not use_bracket:
                message = f"Order sent: {side} {quantity} {symbol} {order_type}."
            else:
                message = (
                    f"Bracket sent: {side} {quantity} {symbol} {order_type} @ {bracket_entry_price} "
                    f"TP={take_profit:.8f} ({tp_pct}%) SL={stop_loss:.8f} ({sl_pct}%)."
                )
            self.order_result.emit(
                {
                    "ok": True,
                    "kind": "order",
                    "message": message,
                    "symbol": symbol,
                    "side": side,
                    "order_type": order_type,
                    "quantity": quantity,
                    "volume": quantity,
                    "limit_price": limit_price,
                    "use_bracket": use_bracket,
                    "take_profit_pct": tp_pct,
                    "stop_loss_pct": sl_pct,
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                }
            )
        except Exception as exc:
            logger.exception("place_order unexpected failure")
            self.failed.emit(str(exc))

    @pyqtSlot(object)
    # Run a what-if preview for a validated order request.
    def preview_order(self, request: Any) -> None:
        logger.debug("preview_order received payload=%r", request)
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
        use_bracket = normalized["use_bracket"]
        tp_pct = normalized["take_profit_pct"]
        sl_pct = normalized["stop_loss_pct"]

        try:
            with self.io_lock:
                if not self.ib_client.is_connected():
                    self.order_result.emit({"ok": False, "kind": "preview", "message": "Not connected to IBKR."})
                    return

                contract = Forex(symbol)
                logger.debug("preview_order built contract=%r", contract)
                self.ib_client.clear_last_error()
                logger.debug("self.ib_client.clear_last_error() before qualify_contract() for preview")
                qualified_contract = self.ib_client.qualify_contract(contract)
                if qualified_contract is None:
                    reason = self.ib_client.get_last_error_text() or "Unable to qualify contract."
                    self.order_result.emit(
                        {
                            "ok": False,
                            "kind": "preview",
                            "message": f"Preview failed for {side} {quantity} {symbol} {order_type} - {reason}",
                        }
                    )
                    return
                logger.debug("preview_order using qualified contract=%r", qualified_contract)
                order = self._build_order(side, order_type, quantity, limit_price)
                self.ib_client.clear_last_error()
                logger.debug("self.ib_client.clear_last_error() before ib.whatIfOrder()")
                what_if = self.ib_client.what_if_order(qualified_contract, order)
                logger.debug("ib.whatIfOrder() response=%r", what_if)
                if what_if is None:
                    reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                    logger.debug("ib.whatIfOrder() failure reason=%s", reason)
                    self.order_result.emit(
                        {
                            "ok": False,
                            "kind": "preview",
                            "message": f"Preview failed for {side} {quantity} {symbol} {order_type} - {reason}",
                        }
                    )
                    return

                preview_suffix = ""
                take_profit = None
                stop_loss = None
                if use_bracket:
                    entry_price = self._resolve_entry_price(normalized)
                    if entry_price is None or entry_price <= 0:
                        self.order_result.emit(
                            {
                                "ok": False,
                                "kind": "preview",
                                "message": "Preview failed: invalid entry price for bracket levels.",
                            }
                        )
                        return
                    take_profit, stop_loss = self._derive_bracket_prices(
                        side=side,
                        entry_price=entry_price,
                        tp_pct=float(tp_pct),
                        sl_pct=float(sl_pct),
                    )
                    preview_suffix = (
                        f" | TP={take_profit:.8f} ({tp_pct}%)"
                        f" SL={stop_loss:.8f} ({sl_pct}%)"
                    )

            logger.debug("preview_order ib_response=%r", self._what_if_debug_payload(what_if))
            init_margin = getattr(what_if, "initMarginChange", "--")
            maint_margin = getattr(what_if, "maintMarginChange", "--")
            commission = getattr(what_if, "commission", "--")
            preview_message = (
                f"Preview {side} {quantity} {symbol} {order_type} | "
                f"InitMargin: {init_margin} MaintMargin: {maint_margin} Commission: {commission}"
                f"{preview_suffix}"
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
                    "volume": quantity,
                    "limit_price": limit_price,
                    "use_bracket": use_bracket,
                    "take_profit_pct": tp_pct,
                    "stop_loss_pct": sl_pct,
                    "take_profit": take_profit,
                    "stop_loss": stop_loss,
                }
            )
        except Exception as exc:
            logger.exception("preview_order unexpected failure")
            self.failed.emit(str(exc))
