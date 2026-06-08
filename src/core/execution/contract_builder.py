"""Pure helpers : preview-leg → IB Contract / Order kwargs.

Returns dialect-free dicts so unit tests don't import ib_insync. The
execution-engine wraps the dicts into ``Contract(**kwargs)`` /
``LimitOrder(**kwargs)`` at runtime.

Spec : ``docs/vol_trading_pca/specs/STEP4_EXECUTION.md`` §7.2 (Contract
construction) + §13 decision 7 (LMT with 0.5 % tolerance).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

# IB FOP trading_class for EUR/USD options (cf. STEP4 §6).
_FOP_TRADING_CLASS = {"EUR": "EUU"}


def _ib_expiry(d: date | str) -> str:
    """IB expiry format = YYYYMMDD (no dashes)."""
    if isinstance(d, str):
        # Accept both ISO ('2026-06-19') and IB ('20260619').
        return d.replace("-", "")
    return d.strftime("%Y%m%d")


def _ib_right(contract_type: str) -> str:
    ct = contract_type.lower()
    if ct in ("call", "c"):
        return "C"
    if ct in ("put", "p"):
        return "P"
    raise ValueError(f"contract_type must be call/put, got {contract_type!r}")


def build_contract_kwargs(
    *,
    contract_type: str,
    expiry: date | str,
    strike: float,
    symbol: str = "EUR",
    exchange: str = "CME",
    currency: str = "USD",
    sec_type: str = "FOP",
) -> dict[str, object]:
    """Build the kwargs for ``ib_insync.Contract`` for a single FOP leg."""
    return {
        "symbol": symbol,
        "secType": sec_type,
        "exchange": exchange,
        "currency": currency,
        "lastTradeDateOrContractMonth": _ib_expiry(expiry),
        "strike": float(strike),
        "right": _ib_right(contract_type),
        "tradingClass": _FOP_TRADING_CLASS.get(symbol, ""),
    }


def build_order_kwargs(
    *,
    side: str, qty: int, limit_price: float, time_in_force: str = "DAY",
) -> dict[str, object]:
    """Build the kwargs for ``ib_insync.LimitOrder`` for a single leg.

    ib_insync.LimitOrder signature : ``LimitOrder(action, totalQuantity, lmtPrice)``.
    The dict shape we return is consumed by ``LimitOrder(**kwargs)`` ; the
    extra ``tif`` field is set after construction.
    """
    side_u = side.upper()
    if side_u not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if limit_price <= 0:
        raise ValueError(f"limit_price must be positive, got {limit_price}")
    return {
        "action": side_u,
        "totalQuantity": int(qty),
        "lmtPrice": float(limit_price),
        "tif": time_in_force,
    }


# --------------------------------------------------------------------------
# Combo (BAG) detection — spec §13 decision 3
# --------------------------------------------------------------------------

def can_use_combo(legs: Sequence[Mapping[str, object]]) -> bool:
    """True if the legs share enough metadata to fly as a single BAG order.

    IB BAG combos require all legs on the same symbol/secType/exchange. We
    additionally require identical expiry — a calendar (two expiries) cannot
    be a single combo (spec §13 decision 3). Strikes / sides / contract_types
    can differ (that's the point — it's a multi-leg structure).
    """
    if len(legs) < 2:
        return False
    first = legs[0]
    needed = ("expiry", "contract_symbol", "contract_exchange", "contract_currency")
    for leg in legs[1:]:
        for key in needed:
            if leg.get(key) != first.get(key):
                return False
    return True
