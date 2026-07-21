"""Units convention for premium / greeks / P&L — the single source of truth.

THE CONVENTION (QNT-1)
======================
Every ``*_usd`` number crossing module boundaries in the
preview → entry-snapshot → monitor → exit-rules chain is a **real US-dollar
amount at full contract notional**. With
``mult = sign(side) × qty × EUR_FOP_MULTIPLIER`` (sign : BUY = +1,
SELL = −1) and the raw Black-Scholes outputs of ``core.pricing.bs`` /
``core.trade_preview.bs_greeks`` :

- **premium / mark**        ``usd = bs_price × EUR_FOP_MULTIPLIER × qty``
  (signed by side ; + = paid / long, − = received / short)
- **vega_usd_per_volpt**    ``= bs_vega × VOLPT × mult``
  ($ P&L per +1 vol-point, i.e. +1 % IV)
- **gamma_usd_per_pip2**    ``= bs_gamma × PIP_SIZE² × mult``  (× 1e-8)
  Gamma P&L is **always** ``0.5 × gamma_usd_per_pip2 × (ΔS_in_pips)²`` —
  spot shocks inside P&L formulas are expressed in **pips**.
- **theta_usd_per_day**     ``= bs_theta_per_year / 365 × mult``
  (``core.pricing.bs.bs_theta`` already returns per-day — no second /365 ;
  ``core.trade_preview.bs_greeks`` returns per-year and divides at the
  aggregation site.)
- **delta_usd**             ``= bs_delta × spot × mult``
  ($ notional exposure ; a *contract-equivalent* delta — what the hedger
  consumes — is ``bs_delta × qty_signed`` and is never named ``*_usd``.)

IB price units
==============
Contract prices quoted to/from Interactive Brokers — limit prices, fill
prices, ``structure_orders.preview_price`` / ``avg_fill_price`` /
``limit_price`` — stay in raw **price points** (IB's ``lmtPrice`` unit
for CME EUR FOPs, e.g. 0.0035). Conversion at the boundary :
``usd = points × EUR_FOP_MULTIPLIER × qty``.
"""
from __future__ import annotations

from typing import Final

# EUR/USD pip.
PIP_SIZE: Final[float] = 1e-4
# One volatility point = 1 % of IV (bs_vega is per 1.00 of vol).
VOLPT: Final[float] = 0.01

# CME EUR options (FOP class EUU) notional per contract — same as the 6E
# future : €125 000. Micro options do not exist ; only futures split sizes.
EUR_FOP_MULTIPLIER: Final[float] = 125_000.0

# EUR notional per CME future contract : 6E (full) / M6E (micro).
FUTURE_MULTIPLIERS: Final[dict[str, int]] = {"full": 125_000, "micro": 12_500}
