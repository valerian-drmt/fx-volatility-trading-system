"""Vol trading structures — Straddle, Risk Reversal, Butterfly, Calendar.

Pure data classes + the leg builder, priced on the ONE kernel
(``core.pricing.bs``) with the ONE tenor map (``core.vol.tenors``). Moved
here from ``engines/execution/structures.py`` (#331, audit 06#2 / 06#3): the
api served the trade preview by importing an engine module that carried its
own Black-76 copy, which needed an import-linter exception and a third
pricer that disagreed with the others at expiry.

Each structure exposes:

- ``legs(forward, surface)`` — list of :class:`Leg` (instrument, side, qty,
  strike, tenor, iv, premium). What the operator sees on the preview.
- ``net_greeks(forward, surface)`` — vega, gamma, theta, delta of the legs
  priced off the surface.

``signal_to_structure(signal, tenor)`` maps a PC signal to the canonical
structure for that factor: PC1 (level) → StraddleATM, PC2 (term_slope) →
CalendarSpread, PC3 skew → RiskReversal25d, PC3 convexity → Butterfly25d.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.pricing.bs import bs_greeks, bs_price
from core.vol.tenors import tenor_years

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
    gamma: float               # per unit of F²
    theta: float               # per day
    delta: float               # net delta (hedged = 0)


def _right(instrument: str) -> str:
    return "C" if instrument.upper().startswith("C") else "P"


def leg_premium(forward: float, strike: float, tenor: str, iv: float, instrument: str) -> float:
    """Premium per contract in price points on the single kernel."""
    T = tenor_years(tenor)
    if T is None:
        return 0.0
    return bs_price(forward, strike, T, iv, _right(instrument))


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
        if atm is None or tenor_years(self.tenor) is None:
            return []
        iv, strike = atm
        return [
            Leg("CALL", self.side, self.qty, strike, self.tenor, iv, leg_premium(forward, strike, self.tenor, iv, "CALL")),
            Leg("PUT", self.side, self.qty, strike, self.tenor, iv, leg_premium(forward, strike, self.tenor, iv, "PUT")),
        ]

    def net_greeks(self, forward: float, surface: dict[str, Any]) -> NetGreeks:
        return sum_greeks(self.legs(forward, surface), forward)


@dataclass(frozen=True)
class RiskReversal25d:
    """Long 25-delta call, short 25-delta put (or inverse) — trades skew."""
    tenor: str
    direction: Literal["LONG_CALL", "LONG_PUT"] = "LONG_CALL"
    qty: int = 10

    def legs(self, forward: float, surface: dict[str, Any]) -> list[Leg]:
        call = _pillar_iv_strike(surface, self.tenor, "25dc")
        put = _pillar_iv_strike(surface, self.tenor, "25dp")
        if call is None or put is None or tenor_years(self.tenor) is None:
            return []
        iv_c, k_c = call
        iv_p, k_p = put
        call_side: Side = "BUY" if self.direction == "LONG_CALL" else "SELL"
        put_side: Side = "SELL" if self.direction == "LONG_CALL" else "BUY"
        return [
            Leg("CALL", call_side, self.qty, k_c, self.tenor, iv_c, leg_premium(forward, k_c, self.tenor, iv_c, "CALL")),
            Leg("PUT", put_side, self.qty, k_p, self.tenor, iv_p, leg_premium(forward, k_p, self.tenor, iv_p, "PUT")),
        ]

    def net_greeks(self, forward: float, surface: dict[str, Any]) -> NetGreeks:
        return sum_greeks(self.legs(forward, surface), forward)


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
        if atm is None or call is None or put is None or tenor_years(self.tenor) is None:
            return []
        iv_atm, k_atm = atm
        iv_c, k_c = call
        iv_p, k_p = put
        wing_side: Side = self.side
        atm_side: Side = "SELL" if self.side == "BUY" else "BUY"
        return [
            Leg("CALL", wing_side, self.qty, k_c, self.tenor, iv_c, leg_premium(forward, k_c, self.tenor, iv_c, "CALL")),
            Leg("PUT", wing_side, self.qty, k_p, self.tenor, iv_p, leg_premium(forward, k_p, self.tenor, iv_p, "PUT")),
            Leg("CALL", atm_side, 2 * self.qty, k_atm, self.tenor, iv_atm,
                leg_premium(forward, k_atm, self.tenor, iv_atm, "CALL")),
        ]

    def net_greeks(self, forward: float, surface: dict[str, Any]) -> NetGreeks:
        return sum_greeks(self.legs(forward, surface), forward)


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
        if near is None or far is None or tenor_years(self.tenor_near) is None or tenor_years(self.tenor_far) is None:
            return []
        iv_n, k_n = near
        iv_f, k_f = far
        near_side: Side = "SELL" if self.side == "BUY" else "BUY"
        far_side: Side = self.side
        return [
            Leg("CALL", near_side, self.qty, k_n, self.tenor_near, iv_n,
                leg_premium(forward, k_n, self.tenor_near, iv_n, "CALL")),
            Leg("CALL", far_side, self.qty, k_f, self.tenor_far, iv_f,
                leg_premium(forward, k_f, self.tenor_far, iv_f, "CALL")),
        ]

    def net_greeks(self, forward: float, surface: dict[str, Any]) -> NetGreeks:
        return sum_greeks(self.legs(forward, surface), forward)


Structure = StraddleATM | RiskReversal25d | Butterfly25d | CalendarSpread


def sum_greeks(legs: list[Leg], forward: float) -> NetGreeks:
    """Signed sum of the legs' greeks: vega per vol point, theta per day."""
    vega = gamma = theta = delta = 0.0
    for leg in legs:
        if leg.instrument == "FUT":
            d = +1.0 if leg.side == "BUY" else -1.0
            delta += d * leg.qty
            continue
        if leg.strike is None or leg.iv is None:
            continue
        T = tenor_years(leg.tenor)
        if T is None:
            continue
        g = bs_greeks(forward, leg.strike, T, leg.iv, _right(leg.instrument))
        sign = +1 if leg.side == "BUY" else -1
        vega += sign * leg.qty * g["vega"] / 100.0      # per unit σ -> per vol point
        gamma += sign * leg.qty * g["gamma"]
        theta += sign * leg.qty * g["theta"] / 365.0    # per year -> per day
        delta += sign * leg.qty * g["delta"]
    return NetGreeks(vega=vega, gamma=gamma, theta=theta, delta=delta)


def signal_to_structure(
    pc_label: str, tenor: str = "3M", direction: str | None = None,
) -> Structure | None:
    """Map a PCA factor label to its canonical trade structure.

    ``direction`` : for PC1 / PC3 "CHEAP" → BUY convexity / volatility ;
    "EXPENSIVE" → SELL. For PC2 slope the sign of the z-score drives
    near/far selection.
    """
    side: Side = "SELL" if direction == "EXPENSIVE" else "BUY"
    if pc_label == "level":
        return StraddleATM(tenor=tenor, side=side)
    if pc_label == "term_slope":
        return CalendarSpread(tenor_near="1M", tenor_far=tenor, side=side)
    if pc_label == "smile":
        return Butterfly25d(tenor=tenor, side=side)
    if pc_label == "skew":
        return RiskReversal25d(tenor=tenor, direction="LONG_CALL" if side == "BUY" else "LONG_PUT")
    return None
