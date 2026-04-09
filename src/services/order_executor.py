from __future__ import annotations

import logging
from typing import Any

from ib_insync import Contract, Forex, LimitOrder, MarketOrder, Order

from services.ib_client import IBClient


logger = logging.getLogger(__name__)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(levelname)s][order_executor] %(message)s"))
    logger.addHandler(_handler)
logger.setLevel(logging.DEBUG)
logger.propagate = False


class OrderExecutor:
    _REJECTED_STATUSES = {"APICANCELLED", "CANCELLED", "INACTIVE"}

    # Initialize execution state.
    def __init__(self, ib_client: IBClient) -> None:
        self.ib_client = ib_client
        self._running = False

    # Mark the worker as ready to process requests.
    def start(self) -> None:
        self._running = True

    # Mark the worker as stopped and reject new requests.
    def stop(self) -> None:
        self._running = False

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

        symbol = OrderExecutor._normalize_symbol(request.get("symbol", ""))
        side = str(request.get("side", "")).strip().upper()
        order_type = str(request.get("order_type", "")).strip().upper()

        try:
            volume = int(request.get("volume", request.get("quantity", 0)))
        except (TypeError, ValueError):
            volume = 0

        limit_price = OrderExecutor._parse_float(request.get("limit_price", 0.0), default=0.0)
        reference_price = OrderExecutor._parse_positive_optional(request.get("reference_price", None))
        use_bracket = bool(request.get("use_bracket", False))
        take_profit_pct = OrderExecutor._parse_positive_optional(request.get("take_profit_pct", None))
        stop_loss_pct = OrderExecutor._parse_positive_optional(request.get("stop_loss_pct", None))

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

    # Execute a validated order request through the IB client.
    def place_order(self, request: Any) -> dict[str, Any]:
        logger.debug("place_order received payload=%r", request)
        if not self._running:
            return {"ok": False, "kind": "order", "message": "Order worker is stopped."}

        normalized = self._normalize_request(request)
        if normalized is None:
            return {"ok": False, "kind": "order", "message": "Invalid order payload."}

        validation_error = self._validate_request(normalized)
        if validation_error is not None:
            return {"ok": False, "kind": "order", "message": validation_error}

        symbol = normalized["symbol"]
        side = normalized["side"]
        order_type = normalized["order_type"]
        quantity = normalized["quantity"]
        limit_price = normalized["limit_price"]
        use_bracket = normalized["use_bracket"]
        tp_pct = normalized["take_profit_pct"]
        sl_pct = normalized["stop_loss_pct"]

        try:
            if not self.ib_client.is_connected():
                return {"ok": False, "kind": "order", "message": "Not connected to IBKR."}

            contract = Forex(symbol)
            logger.debug("Forex() built contract=%r", contract)
            qualified_contract = contract
            logger.debug("order_executor using direct Forex contract without qualification.")

            take_profit = None
            stop_loss = None
            bracket_entry_price = None
            if use_bracket:
                entry_price = self._resolve_entry_price(normalized)
                if entry_price is None or entry_price <= 0:
                    return {
                        
                            "ok": False,
                            "kind": "order",
                            "message": "Cannot derive bracket levels: invalid entry price.",
                        }

                try:
                    take_profit, stop_loss = self._derive_bracket_prices(
                        side=side,
                        entry_price=entry_price,
                        tp_pct=float(tp_pct),
                        sl_pct=float(sl_pct),
                    )
                except Exception as exc:
                    return {
                        
                            "ok": False,
                            "kind": "order",
                            "message": f"Cannot derive bracket levels - {exc}",
                        }

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
                logger.debug("order_executor build_bracket_orders() response=%r", bracket_orders)
                if not bracket_orders:
                    reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                    return {
                        
                            "ok": False,
                            "kind": "order",
                            "message": (
                                f"Bracket build failed ({side} {quantity} {symbol} {order_type}) - {reason}"
                            ),
                        }

                for bracket_order in bracket_orders:
                    logger.debug("order_executor bracket order built=%r", bracket_order)
                    ok, reason = self._submit_one(qualified_contract, bracket_order)
                    if not ok:
                        return {
                            
                                "ok": False,
                                "kind": "order",
                                "message": (
                                    f"Bracket order rejected ({side} {quantity} {symbol} {order_type}) - {reason}"
                                ),
                            }
            else:
                order = self._build_order(side, order_type, quantity, limit_price)
                logger.debug("order_executor single order built=%r", order)
                ok, reason = self._submit_one(qualified_contract, order)
                if not ok:
                    return {
                        
                            "ok": False,
                            "kind": "order",
                            "message": f"Order rejected ({side} {quantity} {symbol} {order_type}) - {reason}",
                        }

            if not use_bracket:
                message = f"Order sent: {side} {quantity} {symbol} {order_type}."
            else:
                message = (
                    f"Bracket sent: {side} {quantity} {symbol} {order_type} @ {bracket_entry_price} "
                    f"TP={take_profit:.8f} ({tp_pct}%) SL={stop_loss:.8f} ({sl_pct}%)."
                )
            return {
                
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
        except Exception:
            logger.exception("place_order unexpected failure")
            raise

    def place_future_order(self, request: dict[str, Any]) -> dict[str, Any]:
        """Place a EUR future MKT order on CME."""
        logger.info("place_future_order ENTER payload=%r", request)
        if not self._running:
            logger.error("place_future_order: executor is stopped")
            return {"ok": False, "kind": "order", "message": "Order executor is stopped."}
        if not self.ib_client.is_connected():
            logger.error("place_future_order: not connected to IBKR")
            return {"ok": False, "kind": "order", "message": "Not connected to IBKR."}

        side = str(request.get("side", "")).strip().upper()
        qty = int(request.get("quantity", 0))
        if side not in ("BUY", "SELL"):
            logger.error("place_future_order: invalid side=%r", side)
            return {"ok": False, "kind": "order", "message": "Invalid side."}
        if qty <= 0:
            logger.error("place_future_order: qty=%d must be > 0", qty)
            return {"ok": False, "kind": "order", "message": "Quantity must be > 0."}

        try:
            # Find front quarterly EUR future
            fut = Contract()
            fut.symbol = "EUR"
            fut.secType = "FUT"
            fut.exchange = "CME"
            fut.currency = "USD"

            logger.info("place_future_order: reqContractDetails for EUR FUT CME...")
            details = self.ib_client.ib.reqContractDetails(fut)
            logger.info("place_future_order: got %d contract details", len(details) if details else 0)
            if not details:
                logger.error("place_future_order: no EUR future found on CME")
                return {"ok": False, "kind": "order", "message": "No EUR future found on CME."}

            # Pick front quarterly
            from datetime import date, timedelta
            today = date.today()
            min_exp = (today + timedelta(days=7)).strftime("%Y%m%d")
            quarterly = [
                d for d in details
                if d.contract.lastTradeDateOrContractMonth >= min_exp
                and int(d.contract.lastTradeDateOrContractMonth[4:6]) in {3, 6, 9, 12}
            ]
            logger.info("place_future_order: %d quarterly contracts after filter (min_exp=%s)", len(quarterly), min_exp)
            if not quarterly:
                logger.error("place_future_order: no quarterly EUR future available")
                return {"ok": False, "kind": "order", "message": "No quarterly EUR future available."}
            quarterly.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)
            qualified = quarterly[0].contract

            logger.info("place_future_order: selected %s conId=%s exp=%s",
                        qualified.localSymbol, qualified.conId, qualified.lastTradeDateOrContractMonth)

            order = Order()
            order.action = side
            order.totalQuantity = qty
            order.orderType = "MKT"
            order.tif = "DAY"

            logger.info("place_future_order: placing order %s %d %s MKT...", side, qty, qualified.localSymbol)
            self.ib_client.clear_last_error()
            trade = self.ib_client.place_order(qualified, order)
            logger.info("place_future_order: place_order returned trade=%r", trade)
            if trade is None:
                reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                logger.error("place_future_order: order rejected reason=%s", reason)
                return {"ok": False, "kind": "order", "message": f"Order rejected: {reason}"}

            status = self._trade_status(trade)
            logger.info("place_future_order: trade status=%s", status)
            if status in self._REJECTED_STATUSES:
                reason = self.ib_client.get_last_error_text() or f"IB status={status}"
                logger.error("place_future_order: order rejected status=%s reason=%s", status, reason)
                return {"ok": False, "kind": "order", "message": f"Order rejected: {reason}"}

            msg = f"Order sent: {side} {qty} {qualified.localSymbol} MKT"
            logger.info("place_future_order: SUCCESS — %s", msg)
            return {
                "ok": True,
                "kind": "order",
                "message": msg,
                "symbol": qualified.localSymbol,
                "side": side,
                "order_type": "MKT",
                "quantity": qty,
            }
        except Exception:
            logger.exception("place_future_order unexpected failure")
            raise

    # Run a what-if preview for a validated order request.
    def preview_order(self, request: Any) -> dict[str, Any]:
        logger.debug("preview_order received payload=%r", request)
        if not self._running:
            return {"ok": False, "kind": "preview", "message": "Order worker is stopped."}

        normalized = self._normalize_request(request)
        if normalized is None:
            return {"ok": False, "kind": "preview", "message": "Invalid order payload."}

        validation_error = self._validate_request(normalized)
        if validation_error is not None:
            return {"ok": False, "kind": "preview", "message": validation_error}

        symbol = normalized["symbol"]
        side = normalized["side"]
        order_type = normalized["order_type"]
        quantity = normalized["quantity"]
        limit_price = normalized["limit_price"]
        use_bracket = normalized["use_bracket"]
        tp_pct = normalized["take_profit_pct"]
        sl_pct = normalized["stop_loss_pct"]

        try:
            if not self.ib_client.is_connected():
                return {"ok": False, "kind": "preview", "message": "Not connected to IBKR."}

            contract = Forex(symbol)
            logger.debug("preview_order built contract=%r", contract)
            self.ib_client.clear_last_error()
            logger.debug("self.ib_client.clear_last_error() before qualify_contract() for preview")
            qualified_contract = self.ib_client.qualify_contract(contract)
            if qualified_contract is None:
                reason = self.ib_client.get_last_error_text() or "Unable to qualify contract."
                return {
                    
                        "ok": False,
                        "kind": "preview",
                        "message": f"Preview failed for {side} {quantity} {symbol} {order_type} - {reason}",
                    }
            logger.debug("preview_order using qualified contract=%r", qualified_contract)
            order = self._build_order(side, order_type, quantity, limit_price)
            self.ib_client.clear_last_error()
            logger.debug("self.ib_client.clear_last_error() before ib.whatIfOrder()")
            what_if = self.ib_client.what_if_order(qualified_contract, order)
            logger.debug("ib.whatIfOrder() response=%r", what_if)
            if what_if is None:
                reason = self.ib_client.get_last_error_text() or "Unknown IB error."
                logger.debug("ib.whatIfOrder() failure reason=%s", reason)
                return {
                    
                        "ok": False,
                        "kind": "preview",
                        "message": f"Preview failed for {side} {quantity} {symbol} {order_type} - {reason}",
                    }

            take_profit = None
            stop_loss = None
            if use_bracket:
                entry_price = self._resolve_entry_price(normalized)
                if entry_price is None or entry_price <= 0:
                    return {
                        
                            "ok": False,
                            "kind": "preview",
                            "message": "Preview failed: invalid entry price for bracket levels.",
                        }
                take_profit, stop_loss = self._derive_bracket_prices(
                    side=side,
                    entry_price=entry_price,
                    tp_pct=float(tp_pct),
                    sl_pct=float(sl_pct),
                )
            logger.debug("preview_order ib_response=%r", self._what_if_debug_payload(what_if))
            init_margin = getattr(what_if, "initMarginChange", "--")
            maint_margin = getattr(what_if, "maintMarginChange", "--")
            commission = getattr(what_if, "commission", "--")
            lines = [
                f"Preview: {side} {quantity} {symbol} {order_type}",
                f"Init Margin: {init_margin}",
                f"Maint Margin: {maint_margin}",
                f"Commission: {commission}",
            ]
            if take_profit is not None:
                lines.append(f"Take Profit: {take_profit:.8f} ({tp_pct}%)")
            if stop_loss is not None:
                lines.append(f"Stop Loss: {stop_loss:.8f} ({sl_pct}%)")
            preview_message = "\n".join(lines)
            return {
                
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
        except Exception:
            logger.exception("preview_order unexpected failure")
            raise
