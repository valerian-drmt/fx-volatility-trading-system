"""Editable-settings catalog — the tunable (arbitrary-but-not-data) knobs of each
desk domain, surfaced in the Settings tab.

Each :class:`ConfigParam` declares one knob: its ``config_scalar`` namespace +
name, a code default (the seed / fallback when no DB row exists), a unit and a
human description. The api layer overlays the live DB values and serves them via
``/admin/settings/{domain}``; consumers (trade gating, /var, /greek-limits …)
read the same ``config_scalar`` rows, so editing a knob changes real behaviour —
these are policy, not data.

Pure module (core contract): no I/O, only the static catalog.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.risk import greek_limits as gl


@dataclass(frozen=True)
class ConfigParam:
    name: str
    namespace: str
    default: float
    unit: str
    description: str


def _greek_params() -> list[ConfigParam]:
    """The greek-limit policy (namespace 'greek_limits'), reusing its catalog."""
    out: list[ConfigParam] = []
    for name, default in gl.CONFIG_DEFAULTS.items():
        unit, desc = gl.CONFIG_META.get(name, ("", ""))
        out.append(ConfigParam(name, "greek_limits", default, unit, desc))
    return out


# domain → ordered list of knobs. trade/signal/risk share the live 'risk'
# namespace already read by trade gating; portfolio owns a 'portfolio' namespace
# read by /var.
DOMAINS: dict[str, list[ConfigParam]] = {
    "risk": _greek_params(),
    "trade": [
        ConfigParam("base_qty", "risk", 10, "count", "Base position size"),
        ConfigParam("max_n_open_structures", "risk", 8, "count", "Max simultaneous open structures"),
        ConfigParam("max_loss_per_trade_pct", "risk", 2, "pct_capital", "Max loss per trade (% of capital)"),
        ConfigParam("book_vega_neutral_threshold", "risk", 2000, "usd", "Vega above this triggers the book penalty"),
        ConfigParam("book_alpha", "risk", 0.3, "weight", "Book penalty exponent (0..1)"),
        ConfigParam("max_book_vega_usd", "risk", 5000, "usd", "Max total book vega"),
        ConfigParam("max_book_vega_per_tenor_usd", "risk", 2000, "usd", "Max vega per single tenor"),
        ConfigParam("starting_capital_usd", "risk", 100000, "usd", "Bootstrap capital for the first preview"),
        ConfigParam("min_liquidity_quoted_size", "risk", 10, "count", "Minimum quoted size on legs"),
        ConfigParam("preview_validity_seconds", "risk", 120, "count", "Trade-preview validity (seconds)"),
    ],
    "signal": [
        ConfigParam("z_threshold_min", "risk", 1.5, "count", "Min |z| considered actionable"),
        ConfigParam("max_z_multiplier", "risk", 2, "count", "Cap on the z-score sizing factor"),
        ConfigParam("max_iv_data_age_seconds", "risk", 120, "count", "IV data must be fresher than this (seconds)"),
    ],
    "portfolio": [
        ConfigParam("var_lookback_days", "portfolio", 504, "days", "Window for the historical-VaR net-liq series"),
        ConfigParam("var_max_gap_days", "portfolio", 3, "days", "Max day-gap that still counts as a 1-day P&L delta"),
    ],
}

DOMAIN_TITLES: dict[str, str] = {
    "trade": "Trade settings",
    "signal": "Signal settings",
    "risk": "Risk settings",
    "portfolio": "Portfolio settings",
}


def param(domain: str, name: str) -> ConfigParam | None:
    for p in DOMAINS.get(domain, []):
        if p.name == name:
            return p
    return None


def validate(p: ConfigParam, value: float) -> str | None:
    """Light, unit-driven bounds. Returns an error message, or None if valid."""
    if value != value:  # NaN
        return f"{p.name} must be a number"
    if p.unit in {"weight", "fraction"} and not 0 <= value <= 1:
        return f"{p.name} must be in [0, 1]"
    if p.unit in {"frac_capital", "frac_spot"} and not 0 < value <= 1:
        return f"{p.name} must be in (0, 1]"
    if p.unit in {"pct_capital", "pct"} and not 0 <= value <= 100:
        return f"{p.name} must be in [0, 100]"
    if p.unit in {"usd", "count", "vol_pts"} and value < 0:
        return f"{p.name} must be ≥ 0"
    if p.unit == "days" and value < 1:
        return f"{p.name} must be ≥ 1"
    return None
