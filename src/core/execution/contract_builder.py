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


def _next_quarterly_yyyymm(d: date | str) -> str:
    """Snap a date to the next CME quarterly contract month (Mar/Jun/Sep/Dec).

    EUR FX futures (6E + M6E) trade primarily on quarterlies. M6E is
    *only* listed for quarterlies. Snapping the user's preview-derived
    expiry (which is a calendar tenor like "now+90d") to the next
    quarterly is the only way IB ``qualifyContractsAsync`` returns a
    matching contract.

    Returns the contract month as ``YYYYMM`` (e.g. ``"202609"``). IB's
    qualify step accepts this short form for futures and resolves it to
    the actual expiry date internally.
    """
    if isinstance(d, str):
        # Accept ISO or YYYYMMDD.
        s = d.replace("-", "")
        y, m = int(s[:4]), int(s[4:6])
    else:
        y, m = d.year, d.month
    quarterlies = [3, 6, 9, 12]
    for qm in quarterlies:
        if qm >= m:
            return f"{y:04d}{qm:02d}"
    # Past December → roll to March of next year.
    return f"{y + 1:04d}03"


def _option_yyyymm(d: date | str) -> str:
    """Reduce a calendar expiry to ``YYYYMM`` for FOP qualification.

    The preview computes expiry as ``today + tenor_dte`` which rarely
    falls on a listed FOP expiry (3rd Friday for monthlies, weekly Fridays
    for short tenors). Passing only ``YYYYMM`` lets IB resolve to the
    standard monthly listing of that contract month.

    Limitation : doesn't pick weekly options. If the operator types a
    1W / 2W tenor, IB will still return the monthly of the same month
    (acceptable — operator can refine later via signal-driven flow).
    """
    if isinstance(d, str):
        s = d.replace("-", "")
        return s[:6]
    return d.strftime("%Y%m")


# CME EUR FOP standard strike grid : 0.005 increments (1.175 / 1.180 / 1.185).
# The vol-engine smile interpolates and can produce off-grid strikes ; the
# live submit snaps to the nearest listed strike so IB qualify succeeds.
_FOP_STRIKE_INCREMENT: float = 0.005


def _snap_fop_strike(strike: float) -> float:
    """Round ``strike`` to the nearest listed CME EUR FOP grid point.

    Listed monthly options trade at ±0.005 increments (e.g. 1.170 / 1.175 /
    1.180 …). Quarterly cycles add intermediate 0.0025 strikes near ATM
    but 0.005 always qualifies. We snap conservatively.
    """
    return round(strike / _FOP_STRIKE_INCREMENT) * _FOP_STRIKE_INCREMENT


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
    strike: float | None = None,
    symbol: str = "EUR",
    exchange: str = "CME",
    currency: str = "USD",
    sec_type: str = "FOP",
) -> dict[str, object]:
    """Build the kwargs for ``ib_insync.Contract`` — FOP (option) or FUT.

    Futures path (``contract_type='future'`` or ``sec_type='FUT'``) :
    strike is ignored, `tradingClass` empty, the `symbol` carries the
    full CME ticker (``6E`` or ``M6E``).
    """
    if contract_type.lower() == "future" or sec_type == "FUT":
        return {
            "symbol": symbol,
            "secType": "FUT",
            "exchange": exchange,
            "currency": currency,
            # Snap to next quarterly month — the only listings IB will
            # qualify for EUR/M6E futures.
            "lastTradeDateOrContractMonth": _next_quarterly_yyyymm(expiry),
        }
    if strike is None:
        raise ValueError("strike required for FOP contract")
    return {
        "symbol": symbol,
        "secType": sec_type,
        "exchange": exchange,
        "currency": currency,
        # YYYYMM (contract month) — IB resolves to the monthly Friday
        # automatically. Avoids "not qualified" when the preview-computed
        # calendar date doesn't land on a listed expiry.
        "lastTradeDateOrContractMonth": _option_yyyymm(expiry),
        # Snap to the 0.005 strike grid so off-grid smile-interpolated
        # strikes (e.g. 1.1797) qualify to the nearest listed strike.
        "strike": round(_snap_fop_strike(float(strike)), 4),
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
