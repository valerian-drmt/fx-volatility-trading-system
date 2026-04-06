from __future__ import annotations

import argparse
import asyncio
from typing import Any

from ib_insync import Forex, IB, MarketOrder, LimitOrder


def _ensure_default_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _build_order(side: str, order_type: str, quantity: int, limit_price: float | None) -> Any:
    if order_type == "MKT":
        order = MarketOrder(side, quantity)
        order.tif = "GTC"
        return order
    if limit_price is None or limit_price <= 0:
        raise ValueError("LMT order requires --limit-price > 0.")
    order = LimitOrder(side, quantity, float(limit_price))
    order.tif = "DAY"
    return order


def _what_if_payload(what_if: Any) -> dict[str, Any]:
    keys = (
        "initMarginBefore", "initMarginChange", "initMarginAfter",
        "maintMarginBefore", "maintMarginChange", "maintMarginAfter",
        "equityWithLoanBefore", "equityWithLoanChange", "equityWithLoanAfter",
        "commission", "minCommission", "maxCommission", "warningText",
    )
    return {k: getattr(what_if, k, "--") for k in keys}


def _trade_payload(trade: Any) -> dict[str, Any]:
    order = getattr(trade, "order", None)
    order_status = getattr(trade, "orderStatus", None)
    contract = getattr(trade, "contract", None)
    return {
        "orderId":      getattr(order, "orderId", None),
        "status":       getattr(order_status, "status", None),
        "filled":       getattr(order_status, "filled", None),
        "remaining":    getattr(order_status, "remaining", None),
        "avgFillPrice": getattr(order_status, "avgFillPrice", None),
        "symbol":       getattr(contract, "symbol", None),
        "secType":      getattr(contract, "secType", None),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host",            default="127.0.0.1")
    parser.add_argument("--port",            type=int,   default=4002)
    parser.add_argument("--client-id",       type=int,   default=1)
    parser.add_argument("--order-type",      choices=("MKT", "LMT"), default="MKT")
    parser.add_argument("--quantity",        type=int,   default=20000)
    parser.add_argument("--limit-price",     type=float, default=None)
    parser.add_argument("--connect-timeout", type=float, default=4.0)
    parser.add_argument("--wait-seconds",    type=float, default=5.0)
    parser.add_argument(
        "--preview-only",
        action="store_true",
        default=False,
        help="Run what-if preview and exit without placing the real order.",
    )
    return parser.parse_args()


def main() -> int:
    _ensure_default_event_loop()
    args = _parse_args()

    ib = IB()
    gateway_errors: list[tuple[int, int, str]] = []

    def _on_error(req_id: int, error_code: int, error_msg: str, _contract: Any) -> None:
        gateway_errors.append((int(req_id), int(error_code), str(error_msg)))

    try:
        ib.errorEvent += _on_error
        print(f"[INFO] Connecting host={args.host} port={args.port} client_id={args.client_id}...")
        connected = ib.connect(
            args.host, args.port,
            clientId=args.client_id,
            readonly=False,
            timeout=float(args.connect_timeout),
        )
        if not connected:
            print("[ERROR] Connection failed.")
            return 1
        print("[INFO] Connected.")

        contract = Forex("EURUSD")
        qualified = ib.qualifyContracts(contract)
        if qualified:
            contract = qualified[0]
        print(f"[INFO] Contract : {contract}")

        order = _build_order(
            side="SELL",
            order_type=args.order_type,
            quantity=args.quantity,
            limit_price=args.limit_price,
        )
        print(f"[INFO] Order    : {order}")

        # --- What-if preview ---
        print("[INFO] Sending preview (what-if)...")
        preview_error_start = len(gateway_errors)
        what_if = ib.whatIfOrder(contract, order)
        ib.sleep(0.5)
        preview_errors = gateway_errors[preview_error_start:]
        if preview_errors:
            print(f"[ERROR] Preview gateway errors : {preview_errors}")
            return 1
        if what_if is None:
            print("[ERROR] Preview failed: empty what-if payload.")
            return 1
        print(f"[INFO] Preview  : {_what_if_payload(what_if)}")

        # --- Stop here if --preview-only ---
        if args.preview_only:
            print("[INFO] --preview-only flag set, skipping real order.")
            return 0

        # --- Real order ---
        print("[INFO] Placing order...")
        error_start = len(gateway_errors)
        trade = ib.placeOrder(contract, order)
        if trade is None:
            print("[ERROR] placeOrder returned None.")
            return 1

        ib.sleep(float(args.wait_seconds))

        errors = gateway_errors[error_start:]
        if errors:
            print(f"[WARN] Gateway errors : {errors}")

        print(f"[INFO] Result   : {_trade_payload(trade)}")
        print("[INFO] Done.")
        return 0

    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    finally:
        if ib.isConnected():
            ib.disconnect()
            print("[INFO] Disconnected.")


if __name__ == "__main__":
    raise SystemExit(main())