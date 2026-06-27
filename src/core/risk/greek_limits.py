"""Greek limit framework — stress-loss budget projected onto each greek axis.

Implements ``greek-limits-spec.md`` §2/§6/§8 (pure math, no I/O):

  * One daily stress-loss appetite ``L* = ALPHA * nav_base`` is projected onto
    each axis by inverting that axis' shock — caps are *derived*, never
    hardcoded: ``greek_cap = beta_axis * L* / shock_axis``.
  * ``nav_base`` is a slow-moving anchor (high-water-mark floor ∨ EWMA), NOT the
    live NAV, so a drawdown does not procyclically tighten every cap at once
    (§6).
  * ``regime_mult`` scales the shocks up (and therefore caps down) as vol rises
    (§8); the caller supplies it.

The api/engine layers own all I/O; this module is import-pure (core contract).
"""
from __future__ import annotations

from dataclasses import dataclass

# §1 — config constants (calm-regime base shocks; scaled by regime_mult).
ALPHA = 0.05  # daily stress-loss appetite as a fraction of the capital base
BETA = {  # allocation of L* across axes (standalone, sums to 1.0)
    "delta": 0.15,
    "vega": 0.50,  # largest: vega is the intended risk of a vol book
    "gamma": 0.25,
    "cross": 0.10,  # vanna + volga, enforced via the scenario grid
}
SHOCK_SPOT = 0.025  # 2.5% ≈ 270 pips, 1-day stress
SHOCK_VOL = 4.0  # vol points, 1-day stress
PIP = 1e-4

# ── Editable risk policy (the "Risk settings" panel) ─────────────────────────
# These are arbitrary policy choices, NOT data — so they belong in config, not
# in code. The values here are the seed defaults; the live values come from
# config_scalar (namespace 'greek_limits') overlaid via ``params=`` below.
CONFIG_DEFAULTS: dict[str, float] = {
    "alpha": ALPHA,
    "beta_delta": BETA["delta"],
    "beta_vega": BETA["vega"],
    "beta_gamma": BETA["gamma"],
    "beta_cross": BETA["cross"],
    "shock_spot": SHOCK_SPOT,
    "shock_vol": SHOCK_VOL,
    "nav_hwm_floor": 0.9,
    "nav_halflife_days": 20.0,
}
# (unit, human description) for each param — drives the settings UI.
CONFIG_META: dict[str, tuple[str, str]] = {
    "alpha": ("frac_capital", "Daily stress-loss appetite as a fraction of the capital base (L* = α·nav_base)"),
    "beta_delta": ("weight", "Share of the loss budget allocated to the delta axis"),
    "beta_vega": ("weight", "Share of the loss budget allocated to the vega axis (largest on a vol book)"),
    "beta_gamma": ("weight", "Share of the loss budget allocated to the gamma axis"),
    "beta_cross": ("weight", "Share allocated to cross greeks (vanna + volga)"),
    "shock_spot": ("frac_spot", "1-day spot stress (0.025 = 2.5% ≈ 270 pips)"),
    "shock_vol": ("vol_pts", "1-day vol stress, in vol points"),
    "nav_hwm_floor": ("fraction", "nav_base floor as a fraction of the high-water mark (anti-procyclicality)"),
    "nav_halflife_days": ("days", "EWMA half-life (days) for the nav_base anchor"),
}


@dataclass(frozen=True)
class GreekCaps:
    """Derived caps + the inputs they came from (all USD unless noted)."""

    delta_usd: float  # net delta-equivalent notional cap
    vega_usd: float  # total book vega cap, per vol point
    gamma_pip: float  # gamma cap, USD delta-drift per 1-pip spot move
    cross_usd: float  # vanna+volga budget (scenario-grid enforced)
    loss_budget_usd: float  # L* = ALPHA * nav_base
    nav_base_usd: float
    spot: float
    regime_mult: float


def compute_caps(
    nav_base: float,
    spot: float,
    regime_mult: float = 1.0,
    params: dict[str, float] | None = None,
) -> GreekCaps:
    """Project the stress-loss budget onto the delta / vega / gamma / cross axes.

    ``params`` overlays :data:`CONFIG_DEFAULTS` (the live values from the Risk
    settings panel); missing keys fall back to the defaults.

    Gamma convention (must match the live feed): ``gamma_pip`` = change in
    delta-equivalent USD notional per 1-pip spot move. Stress P&L over ``n`` pips
    = ``0.5 * gamma_pip * n^2 * (PIP / spot)`` ⇒ inverting for the cap gives the
    ``1/(s^2 * spot * 1e4)`` factor below.

    At defaults, nav_base=812_000, spot=1.08, regime_mult=1 → delta≈$243.6k,
    vega≈$5,075, gamma≈$3,007/pip (spec §2 sanity values).
    """
    p = {**CONFIG_DEFAULTS, **(params or {})}
    s = p["shock_spot"] * regime_mult  # caps auto-tighten as regime_mult rises
    v = p["shock_vol"] * regime_mult
    if nav_base <= 0 or spot <= 0 or regime_mult <= 0 or s <= 0 or v <= 0:
        return GreekCaps(0.0, 0.0, 0.0, 0.0, 0.0, max(nav_base, 0.0), spot, regime_mult)
    loss = p["alpha"] * nav_base
    return GreekCaps(
        delta_usd=p["beta_delta"] * loss / s,
        vega_usd=p["beta_vega"] * loss / v,
        gamma_pip=2 * p["beta_gamma"] * loss / (s**2 * spot * 1e4),
        cross_usd=p["beta_cross"] * loss,
        loss_budget_usd=loss,
        nav_base_usd=nav_base,
        spot=spot,
        regime_mult=regime_mult,
    )


def ewma(values: list[float], halflife: float) -> float | None:
    """Exponentially-weighted mean, most-recent value weighted highest.

    ``values`` in chronological order (oldest → newest). ``None`` if empty.
    """
    if not values or halflife <= 0:
        return None
    decay = 0.5 ** (1.0 / halflife)
    num = 0.0
    den = 0.0
    w = 1.0
    for x in reversed(values):  # newest first → highest weight
        num += w * x
        den += w
        w *= decay
    return num / den if den else None


def nav_base(nav_series: list[float], hwm_floor: float = 0.9, halflife: float = 20.0) -> float | None:
    """Slow-moving capital anchor for cap sizing (§6).

    ``max(high_water_mark * hwm_floor, ewma(nav, halflife))`` over the daily
    net-liq ``nav_series`` (chronological). Decouples the limit denominator from
    the same drawdown the limits exist to protect against. ``None`` if no data.
    """
    if not nav_series:
        return None
    hwm = max(nav_series)
    smoothed = ewma(nav_series, halflife)
    if smoothed is None:
        return hwm * hwm_floor
    return max(hwm * hwm_floor, smoothed)


def regime_mult(current_vol: float | None, calm_baseline_vol: float, lo: float = 1.0, hi: float = 3.0) -> float:
    """Shock multiplier from prevailing vs calm vol (§8), clamped to [lo, hi].

    ``1.0`` (no scaling) when either input is missing/zero.
    """
    if not current_vol or calm_baseline_vol <= 0:
        return lo
    return max(lo, min(hi, current_vol / calm_baseline_vol))
