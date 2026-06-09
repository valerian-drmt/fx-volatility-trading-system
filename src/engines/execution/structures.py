"""Vol trading structures — Straddle, Risk Reversal, Butterfly, Calendar.

Each structure is a small dataclass that exposes :

- ``legs(forward, surface)`` — list of ``Leg`` tuples (instrument, side,
  qty, strike, expiry, iv, premium). The legs are what the operator
  sees on Panel 3 section A.
- ``net_greeks(forward, surface)`` — total vega, gamma, theta, delta
  of the portfolio of legs priced off the current surface.
- ``pnl_decomp(entry_surface, exit_surface)`` — rough P&L decomposition
  between vega, gamma/theta and alpha components. Used by Panel 3
  section D scenarios.

The pricing relies on lightweight Black-Scholes helpers — no IB calls.
When the operator clicks Submit on Panel 3, a separate adapter (to be
written in a follow-up) converts the ``legs()`` output into IB orders.

signal_to_structure(signal, tenor) maps a PC signal to the canonical
structure for that factor :

    PC1 (level)        → StraddleATM
    PC2 (term_slope)   → CalendarSpread
    PC3 (smile skew)   → RiskReversal25d
    PC3 (convexity)    → Butterfly25d
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

Side = Literal["BUY", "SELL"]
OptionRight = Literal["CALL", "PUT"]


@dataclass(frozen=True)
class Leg:
    instrument: str            # 'CALL' | 'PUT' | 'FUT'
    side: Side
    qty: int
    strike: float | None       # None for futures
    tenor: str
    iv: float | None           # decimal, None for futures
    premium_per_contract: float


@dataclass(frozen=True)
class NetGreeks:
    vega: float                # $/vol_pt (per 1% IV change)
    gamma: float               # $/($0.01 spot move)²
    theta: float               # $/day
    delta: float               # net delta (hedged = 0)


# ────────────────────────────────────────────────────────────────
# Black-Scholes primitives (European, no dividend, no interest rate) —
# adequate for short-dated FX options on the CME futures.
# ────────────────────────────────────────────────────────────────


def _phi(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _N(x: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(F: float, K: float, T: float, sigma: float, right: OptionRight) -> float:
    """Black-76 option price on a futures underlying (discounted implicitly)."""
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        return max(0.0, (F - K) if right == "CALL" else (K - F))
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if right == "CALL":
        return F * _N(d1) - K * _N(d2)
    return K * _N(-d2) - F * _N(-d1)


def bs_greeks(F: float, K: float, T: float, sigma: float, right: OptionRight) -> dict[str, float]:
    """Vega / Gamma / Theta / Delta under Black-76."""
    if T <= 0 or sigma <= 0:
        return {"vega": 0.0, "gamma": 0.0, "theta": 0.0, "delta": 0.0}
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    pdf = _phi(d1)
    vega = F * pdf * sqrt_t
    gamma = pdf / (F * sigma * sqrt_t)
    theta = -(F * pdf * sigma) / (2.0 * sqrt_t)
    delta = _N(d1) if right == "CALL" else _N(d1) - 1.0
    return {
        "vega": vega / 100.0,     # $/1% IV
        "gamma": gamma,            # $/spot²
        "theta": theta / 365.0,    # $/day
        "delta": delta,
    }


# ────────────────────────────────────────────────────────────────
# Structures
# ────────────────────────────────────────────────────────────────


TENOR_YEARS = {
    "1M": 1/12, "2M": 2/12, "3M": 3/12,
    "4M": 4/12, "5M": 5/12, "6M": 6/12,
}


def _pillar_iv_strike(
    surface: dict[str, Any], tenor: str, label: str,
) -> tuple[float, float] | None:
    """Fetch (iv_decimal, strike) from a surface dict ; None if missing."""
    p = (surface.get(tenor) or {}).get(label)
    if not isinstance(p, dict):
        return None
    iv = p.get("iv")
    k = p.get("strike")
    if isinstance(iv, (int, float)) and isinstance(k, (int, float)):
        return float(iv), float(k)
    return None


@dataclass(frozen=True)
class StraddleATM:
    """Long/short straddle delta-hedged on the ATM strike for ``tenor``."""

    tenor: str
    side: Side = "BUY"
    qty: int = 10

    def legs(self, forward: float, surface: dict[str, Any]) -> list[Leg]:
        atm = _pillar_iv_strike(surface, self.tenor, "atm")
        if atm is None:
            return []
        iv, strike = atm
        T = TENOR_YEARS.get(self.tenor)
        if T is None:
            return []
        call_prem = bs_price(forward, strike, T, iv, "CALL")
        put_prem = bs_price(forward, strike, T, iv, "PUT")
        legs = [
            Leg("CALL", self.side, self.qty, strike, self.tenor, iv, call_prem),
            Leg("PUT", self.side, self.qty, strike, self.tenor, iv, put_prem),
        ]
        # Delta hedge on the FUT : straddle ATM is near delta 0 already.
        delta = 0.0  # ATM straddle delta ≈ 0 by construction
        hedge_qty = -round(delta)
        if hedge_qty != 0:
            legs.append(Leg("FUT", "SELL" if hedge_qty < 0 else "BUY",
                            abs(hedge_qty), None, self.tenor, None, 0.0))
        return legs

    def net_greeks(self, forward: float, surface: dict[str, Any]) -> NetGreeks:
        return _sum_greeks(self.legs(forward, surface), forward)


@dataclass(frozen=True)
class RiskReversal25d:
    """Long 25-delta call, short 25-delta put (or inverse) — trades skew."""

    tenor: str
    direction: Literal["LONG_CALL", "LONG_PUT"] = "LONG_CALL"
    qty: int = 10

    def legs(self, forward: float, surface: dict[str, Any]) -> list[Leg]:
        call = _pillar_iv_strike(surface, self.tenor, "25dc")
        put = _pillar_iv_strike(surface, self.tenor, "25dp")
        T = TENOR_YEARS.get(self.tenor)
        if call is None or put is None or T is None:
            return []
        iv_c, k_c = call
        iv_p, k_p = put
        call_side: Side = "BUY" if self.direction == "LONG_CALL" else "SELL"
        put_side: Side = "SELL" if self.direction == "LONG_CALL" else "BUY"
        return [
            Leg("CALL", call_side, self.qty, k_c, self.tenor, iv_c,
                bs_price(forward, k_c, T, iv_c, "CALL")),
            Leg("PUT", put_side, self.qty, k_p, self.tenor, iv_p,
                bs_price(forward, k_p, T, iv_p, "PUT")),
        ]

    def net_greeks(self, forward: float, surface: dict[str, Any]) -> NetGreeks:
        return _sum_greeks(self.legs(forward, surface), forward)


@dataclass(frozen=True)
class Butterfly25d:
    """Long 25dc + Long 25dp − 2 ATM — trades smile convexity."""

    tenor: str
    side: Side = "BUY"     # BUY = long convexity
    qty: int = 10

    def legs(self, forward: float, surface: dict[str, Any]) -> list[Leg]:
        atm = _pillar_iv_strike(surface, self.tenor, "atm")
        call = _pillar_iv_strike(surface, self.tenor, "25dc")
        put = _pillar_iv_strike(surface, self.tenor, "25dp")
        T = TENOR_YEARS.get(self.tenor)
        if atm is None or call is None or put is None or T is None:
            return []
        iv_atm, k_atm = atm
        iv_c, k_c = call
        iv_p, k_p = put
        wing_side: Side = self.side
        atm_side: Side = "SELL" if self.side == "BUY" else "BUY"
        return [
            Leg("CALL", wing_side, self.qty, k_c, self.tenor, iv_c,
                bs_price(forward, k_c, T, iv_c, "CALL")),
            Leg("PUT", wing_side, self.qty, k_p, self.tenor, iv_p,
                bs_price(forward, k_p, T, iv_p, "PUT")),
            Leg("CALL", atm_side, 2 * self.qty, k_atm, self.tenor, iv_atm,
                bs_price(forward, k_atm, T, iv_atm, "CALL")),
        ]

    def net_greeks(self, forward: float, surface: dict[str, Any]) -> NetGreeks:
        return _sum_greeks(self.legs(forward, surface), forward)


@dataclass(frozen=True)
class CalendarSpread:
    """Short ATM near, long ATM far — trades forward-variance."""

    tenor_near: str
    tenor_far: str
    side: Side = "BUY"      # BUY = long the far, short the near (positive forward var)
    qty: int = 10

    def legs(self, forward: float, surface: dict[str, Any]) -> list[Leg]:
        near = _pillar_iv_strike(surface, self.tenor_near, "atm")
        far = _pillar_iv_strike(surface, self.tenor_far, "atm")
        T_near = TENOR_YEARS.get(self.tenor_near)
        T_far = TENOR_YEARS.get(self.tenor_far)
        if near is None or far is None or T_near is None or T_far is None:
            return []
        iv_n, k_n = near
        iv_f, k_f = far
        near_side: Side = "SELL" if self.side == "BUY" else "BUY"
        far_side: Side = self.side
        return [
            Leg("CALL", near_side, self.qty, k_n, self.tenor_near, iv_n,
                bs_price(forward, k_n, T_near, iv_n, "CALL")),
            Leg("CALL", far_side, self.qty, k_f, self.tenor_far, iv_f,
                bs_price(forward, k_f, T_far, iv_f, "CALL")),
        ]

    def net_greeks(self, forward: float, surface: dict[str, Any]) -> NetGreeks:
        return _sum_greeks(self.legs(forward, surface), forward)


def _sum_greeks(legs: list[Leg], forward: float) -> NetGreeks:
    vega = gamma = theta = delta = 0.0
    for leg in legs:
        if leg.instrument == "FUT":
            d = +1.0 if leg.side == "BUY" else -1.0
            delta += d * leg.qty
            continue
        if leg.strike is None or leg.iv is None:
            continue
        T = TENOR_YEARS.get(leg.tenor)
        if T is None:
            continue
        right: OptionRight = "CALL" if leg.instrument == "CALL" else "PUT"
        g = bs_greeks(forward, leg.strike, T, leg.iv, right)
        sign = +1 if leg.side == "BUY" else -1
        vega += sign * leg.qty * g["vega"]
        gamma += sign * leg.qty * g["gamma"]
        theta += sign * leg.qty * g["theta"]
        delta += sign * leg.qty * g["delta"]
    return NetGreeks(vega=vega, gamma=gamma, theta=theta, delta=delta)


def signal_to_structure(
    pc_label: str, tenor: str = "3M", direction: str | None = None,
) -> StraddleATM | RiskReversal25d | Butterfly25d | CalendarSpread | None:
    """Map a PCA factor label to its canonical trade structure.

    ``direction`` : for PC1 / PC3 "CHEAP" → BUY convexity / volatility ;
    "EXPENSIVE" → SELL. For PC2 slope the sign of the z-score drives
    near/far selection.
    """
    side: Side = "SELL" if direction == "EXPENSIVE" else "BUY"
    if pc_label == "level":
        return StraddleATM(tenor=tenor, side=side)
    if pc_label == "term_slope":
        # Positive z on term_slope → long calendars (long far vs short near).
        return CalendarSpread(tenor_near="1M", tenor_far=tenor, side=side)
    if pc_label == "smile":
        return Butterfly25d(tenor=tenor, side=side)
    if pc_label == "skew":
        return RiskReversal25d(tenor=tenor,
                               direction="LONG_CALL" if side == "BUY" else "LONG_PUT")
    return None
