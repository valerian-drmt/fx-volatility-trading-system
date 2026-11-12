"""Per-position greeks in desk units: ONE scaling path (#313, audit 02#7 / 03#1).

The risk engine scaled greeks twice: ``_persist_position_snapshots`` with the
contract multiplier and the display scalings, ``_aggregate_greeks`` (the
published ``risk_update`` payload) with neither, so the same 10-lot call was
``delta 5.05`` on the bus and ``630 718`` in the table (R3-07), and the
persisted ``delta_usd`` was EUR notional under a USD name (R3-13). Both
paths now call :func:`position_greeks` and sum its output.

Units (see ``docs/vol-modeling/units.md``; ``qty`` is SIGNED, ``mult`` is the
contract notional in EUR, ``F`` the published spot / forward):

    delta_eur              = bs_delta × qty × mult          EUR-equivalent exposure
    delta_usd              = delta_eur × F                  USD exposure (what "$ delta" means)
    gamma_usd_per_pip      = bs_gamma × qty × mult × PIP    change of delta_eur per 1 pip
    vega_usd_per_volpt     = bs_vega  × qty × mult × VOLPT  $ per +1 vol point
    theta_usd_per_day      = bs_theta × qty × mult          $ per calendar day (bs_theta is per day)
    vanna_usd_per_volpt    = bs_vanna × qty × mult × VOLPT  change of delta_eur per +1 vol point
    volga_usd_per_volpt2   = bs_volga × qty × mult × VOLPT² change of vega per +1 vol point

A future is delta one: ``delta_eur = qty × mult``, everything else 0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from core.pricing.bs import bs_delta, bs_gamma, bs_theta, bs_vanna, bs_vega, bs_volga
from core.units import PIP_SIZE, VOLPT


@dataclass(frozen=True)
class PositionGreeks:
    delta_eur: float
    delta_usd: float
    gamma_usd_per_pip: float
    vega_usd_per_volpt: float
    theta_usd_per_day: float
    vanna_usd_per_volpt: float
    volga_usd_per_volpt2: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


ZERO = PositionGreeks(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def future_greeks(qty: float, mult: float, F: float) -> PositionGreeks:
    d_eur = qty * mult
    return PositionGreeks(d_eur, d_eur * F, 0.0, 0.0, 0.0, 0.0, 0.0)


def option_greeks(
    *, qty: float, mult: float, F: float, K: float, T: float, iv: float, right: str,
) -> PositionGreeks:
    qm = qty * mult
    d_eur = qm * bs_delta(F, K, T, iv, right)
    return PositionGreeks(
        delta_eur=d_eur,
        delta_usd=d_eur * F,
        gamma_usd_per_pip=qm * bs_gamma(F, K, T, iv) * PIP_SIZE,
        vega_usd_per_volpt=qm * bs_vega(F, K, T, iv) * VOLPT,
        theta_usd_per_day=qm * bs_theta(F, K, T, iv, right),
        vanna_usd_per_volpt=qm * bs_vanna(F, K, T, iv) * VOLPT,
        volga_usd_per_volpt2=qm * bs_volga(F, K, T, iv) * VOLPT * VOLPT,
    )


def position_greeks(
    *, instrument_type: str, right: str | None, qty: float, mult: float, F: float,
    K: float | None, T: float | None, iv: float | None,
) -> PositionGreeks | None:
    """Dispatch on instrument. None when an option cannot be priced (no
    strike / expiry / IV): the caller decides whether to count it as missing."""
    if instrument_type in ("FUTURE", "SPOT"):   # delta-one: spot is mult 1
        return future_greeks(qty, mult, F)
    if right in ("C", "P") and K and K > 0 and T and T > 0 and iv and iv > 0:
        return option_greeks(qty=qty, mult=mult, F=F, K=K, T=T, iv=iv, right=right)
    return None


def sum_greeks(items: list[PositionGreeks]) -> PositionGreeks:
    if not items:
        return ZERO
    return PositionGreeks(
        delta_eur=sum(g.delta_eur for g in items),
        delta_usd=sum(g.delta_usd for g in items),
        gamma_usd_per_pip=sum(g.gamma_usd_per_pip for g in items),
        vega_usd_per_volpt=sum(g.vega_usd_per_volpt for g in items),
        theta_usd_per_day=sum(g.theta_usd_per_day for g in items),
        vanna_usd_per_volpt=sum(g.vanna_usd_per_volpt for g in items),
        volga_usd_per_volpt2=sum(g.volga_usd_per_volpt2 for g in items),
    )
