"""Step 3 — Trade preview engine.

Pure-Python module : numpy / dataclasses, no IB / Redis / DB access.
DB lookups (signal, surface, book, risk_limits) happen in the api router
which then calls the helpers below.

Cf. docs/vol_trading_pca/specs/STEP3_TRADE_PREVIEW.md §7.

Modules collapsed in one file (vs spec §7.1-7.7 split) for compactness :
this entire file is the single dependency of the api router. Each
top-level function maps 1:1 to a section of the spec.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from core.products import product_label_from_symbol

# 6-tenor canonical grid
TENOR_TO_DTE = {"1M": 30, "2M": 60, "3M": 90, "4M": 120, "5M": 150, "6M": 180}
DELTA_PILLARS = ("10dp", "25dp", "atm", "25dc", "10dc")

# ────────────────────────────────────────────────────────────────
# Black-76 primitives (futures-style — same as engines/execution/structures.py)
# ────────────────────────────────────────────────────────────────


def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _N(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(F: float, K: float, T: float, sigma: float, right: str) -> float:
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        return max(0.0, (F - K) if right == "call" else (K - F))
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if right == "call":
        return F * _N(d1) - K * _N(d2)
    return K * _N(-d2) - F * _N(-d1)


def bs_greeks(F: float, K: float, T: float, sigma: float, right: str) -> dict[str, float]:
    """Returns delta / gamma / vega / theta in raw (unscaled) units.

    Units :
      delta  : ∂P/∂F             — fraction (call ≈ 0..1)
      gamma  : ∂²P/∂F²           — per unit of F
      vega   : ∂P/∂σ             — per unit of σ (NOT per vol-pt)
      theta  : ∂P/∂t (per year)
    """
    if T <= 0 or sigma <= 0 or F <= 0 or K <= 0:
        return {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    delta_call = _N(d1)
    delta = delta_call if right == "call" else delta_call - 1.0
    gamma = _phi(d1) / (F * sigma * sqrt_t)
    vega = F * _phi(d1) * sqrt_t
    # Black-76 with zero interest rate has symmetric theta for call/put.
    theta = -F * _phi(d1) * sigma / (2.0 * sqrt_t)
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


# ────────────────────────────────────────────────────────────────
# Structure builder — matches the seed in migration 013
# ────────────────────────────────────────────────────────────────


# Structure catalog + leg template per spec §5.1. Source of truth for both :
#   - the trade-preview builder (uses ``legs``, ``vega_sign``, etc.)
#   - the ``/api/v1/trade/structures`` endpoint, which surfaces entries
#     marked ``in_catalog: True`` (the 6 main PCA-actionable structures).
# A mirroring ``structure_definition_ref`` DB table existed (seeded by
# migration 013 from a list identical to this dict) until migration 039
# dropped it.
TEMPLATES: dict[str, dict[str, Any]] = {
    "straddle_atm": {
        "display": "Long straddle ATM",
        "requires_delta_hedge": True,
        "vega_sign": "positive",
        "in_catalog": True,
        "typical_gamma_sign": "positive",
        "typical_theta_sign": "negative",
        "description": "Buy ATM call + ATM put",
        "rationale_for_pc": "PC1 CHEAP : level low → buy vol",
        "legs": [
            {"contract_type": "call", "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
            {"contract_type": "put",  "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
        ],
    },
    "short_strangle": {
        "display": "Short OTM strangle",
        "requires_delta_hedge": True,
        "vega_sign": "negative",
        "in_catalog": True,
        "typical_gamma_sign": "negative",
        "typical_theta_sign": "positive",
        "description": "Sell 25d strangle",
        "rationale_for_pc": "PC1 EXPENSIVE : level high → sell vol",
        "legs": [
            {"contract_type": "call", "delta_pillar": "25dc", "side": "SELL", "qty_factor": 1},
            {"contract_type": "put",  "delta_pillar": "25dp", "side": "SELL", "qty_factor": 1},
        ],
    },
    "calendar_long": {
        "display": "Calendar buy long-dated",
        "requires_delta_hedge": True,
        "vega_sign": "positive",
        "in_catalog": True,
        "typical_gamma_sign": "neutral",
        "typical_theta_sign": "neutral",
        "description": "Sell near, buy far",
        "rationale_for_pc": "PC2 CHEAP : term inverted",
        "legs": [
            {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "near", "side": "SELL", "qty_factor": 1},
            {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "far",  "side": "BUY",  "qty_factor": 1},
        ],
    },
    "calendar_short": {
        "display": "Calendar sell long-dated",
        "requires_delta_hedge": True,
        "vega_sign": "negative",
        "in_catalog": True,
        "typical_gamma_sign": "neutral",
        "typical_theta_sign": "neutral",
        "description": "Buy near, sell far",
        "rationale_for_pc": "PC2 EXPENSIVE : term steep",
        "legs": [
            {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "near", "side": "BUY",  "qty_factor": 1},
            {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "far",  "side": "SELL", "qty_factor": 1},
        ],
    },
    "long_butterfly_25d": {
        "display": "Long butterfly (10d wings)",
        "requires_delta_hedge": True,
        "vega_sign": "neutral",
        "in_catalog": True,
        "typical_gamma_sign": "neutral",
        "typical_theta_sign": "neutral",
        "description": "Long wings, short body",
        "rationale_for_pc": "PC3 CHEAP : wings cheap",
        "legs": [
            {"contract_type": "call", "delta_pillar": "10dc", "side": "BUY",  "qty_factor": 1, "overridable": True},
            {"contract_type": "call", "delta_pillar": "atm",  "side": "SELL", "qty_factor": 2, "overridable": False},
            {"contract_type": "call", "delta_pillar": "10dp", "side": "BUY",  "qty_factor": 1, "overridable": True},
        ],
    },
    "short_butterfly_25d": {
        "display": "Short butterfly (10d wings)",
        "requires_delta_hedge": True,
        "vega_sign": "neutral",
        "in_catalog": True,
        "typical_gamma_sign": "neutral",
        "typical_theta_sign": "neutral",
        "description": "Short wings, long body",
        "rationale_for_pc": "PC3 EXPENSIVE : wings rich",
        "legs": [
            {"contract_type": "call", "delta_pillar": "10dc", "side": "SELL", "qty_factor": 1, "overridable": True},
            {"contract_type": "call", "delta_pillar": "atm",  "side": "BUY",  "qty_factor": 2, "overridable": False},
            {"contract_type": "call", "delta_pillar": "10dp", "side": "SELL", "qty_factor": 1, "overridable": True},
        ],
    },
    # ── Off-strategy variants — exposed in the UI but flagged "not in strategy"
    "short_straddle_atm": {
        "display": "Short straddle ATM",
        "requires_delta_hedge": True,
        "vega_sign": "negative",
        "legs": [
            {"contract_type": "call", "delta_pillar": "atm", "side": "SELL", "qty_factor": 1},
            {"contract_type": "put",  "delta_pillar": "atm", "side": "SELL", "qty_factor": 1},
        ],
    },
    "long_strangle_25d": {
        "display": "Long 25Δ strangle",
        "requires_delta_hedge": True,
        "vega_sign": "positive",
        "legs": [
            {"contract_type": "call", "delta_pillar": "25dc", "side": "BUY", "qty_factor": 1},
            {"contract_type": "put",  "delta_pillar": "25dp", "side": "BUY", "qty_factor": 1},
        ],
    },
    "future_buy": {
        "display": "Buy 6E future (delta hedge)",
        "requires_delta_hedge": False,
        "vega_sign": "neutral",
        "legs": [
            {"contract_type": "future", "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
        ],
    },
    "future_sell": {
        "display": "Sell 6E future (delta hedge)",
        "requires_delta_hedge": False,
        "vega_sign": "neutral",
        "legs": [
            {"contract_type": "future", "delta_pillar": "atm", "side": "SELL", "qty_factor": 1},
        ],
    },
    # ── Vanilla single-leg options (delta_pillar + strike fully user-driven)
    "vanilla_call": {
        "display": "Long call",
        "requires_delta_hedge": True,
        "vega_sign": "positive",
        "legs": [
            {"contract_type": "call", "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
        ],
    },
    "short_vanilla_call": {
        "display": "Short call",
        "requires_delta_hedge": True,
        "vega_sign": "negative",
        "legs": [
            {"contract_type": "call", "delta_pillar": "atm", "side": "SELL", "qty_factor": 1},
        ],
    },
    "vanilla_put": {
        "display": "Long put",
        "requires_delta_hedge": True,
        "vega_sign": "positive",
        "legs": [
            {"contract_type": "put", "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
        ],
    },
    "short_vanilla_put": {
        "display": "Short put",
        "requires_delta_hedge": True,
        "vega_sign": "negative",
        "legs": [
            {"contract_type": "put", "delta_pillar": "atm", "side": "SELL", "qty_factor": 1},
        ],
    },
}


# Structures recommended by the PCA strategy (per migration 011 seed).
# Used by the UI to flag off-strategy picks (it does not block them — user can
# still trade hedges or contrarian structures, just with a "not in strategy" tag).
IN_STRATEGY_STRUCTURES: frozenset[str] = frozenset({
    "straddle_atm", "short_strangle",
    "calendar_long", "calendar_short",
    "long_butterfly_25d", "short_butterfly_25d",
})


@dataclass(frozen=True)
class Leg:
    leg_idx: int
    contract_type: Literal["call", "put", "future"]
    tenor: str
    expiry: str
    dte: int
    strike: float | None
    qty_factor: int
    side: Literal["BUY", "SELL"]
    entry_iv_pct: float | None


@dataclass(frozen=True)
class Structure:
    type: str
    reference_tenor: str
    tenor_far: str | None
    legs: list[Leg]
    requires_delta_hedge: bool
    vega_sign: str
    # CME EUR/USD future contract size when type ∈ {future_buy, future_sell}.
    # 'full' = 6E (€125 000), 'micro' = M6E (€12 500). None for non-future.
    future_contract_size: Literal["full", "micro"] | None = None
    # User-friendly product label (cf. core.products / migration 032).
    # Computed at build time so the preview JSON carries it through to
    # the frontend OrderRow drawer.
    product_label: str | None = None


# Multiplier in EUR notional per CME contract.
FUTURE_MULTIPLIERS: dict[str, int] = {"full": 125_000, "micro": 12_500}
# IB ``Contract.symbol`` convention : EUR for full size 6E, M6E for micro.
# Cf. src/shared/contracts.py ContractSpec.symbol.
FUTURE_IB_SYMBOLS: dict[str, str] = {"full": "EUR", "micro": "M6E"}
# Trader-friendly label shown in the UI (the IB Contract.symbol is not what
# the operator reads on a chart — "6E" / "M6E" matches the ticker).
FUTURE_DISPLAY_SYMBOLS: dict[str, str] = {"full": "6E", "micro": "M6E"}
# Commission per round-trip-side (entry only) in USD. IB published rates.
FUTURE_COMMISSION_USD: dict[str, float] = {"full": 2.40, "micro": 0.60}

# CME EUR options (FOP class EUU) notional per contract — same as 6E future.
EUR_FOP_MULTIPLIER: float = 125_000.0


def parse_recommendation(rec: str | None) -> tuple[str, str, str | None]:
    """Parse 'straddle_atm_3M' or 'calendar_long_1M_3M' → (type, near, far|None)."""
    if not rec:
        raise ValueError("empty recommendation")
    toks = rec.split("_")
    # Calendar form first : *_<near>_<far> with both being tenors.
    if (
        len(toks) >= 4
        and toks[-1].upper() in TENOR_TO_DTE
        and toks[-2].upper() in TENOR_TO_DTE
    ):
        return "_".join(toks[:-2]), toks[-2].upper(), toks[-1].upper()
    # Single-tenor form : *_<tenor>
    if len(toks) >= 2 and toks[-1].upper() in TENOR_TO_DTE:
        return "_".join(toks[:-1]), toks[-1].upper(), None
    raise ValueError(f"cannot parse recommendation: {rec!r}")


def _resolve_tenor(template_leg: dict, near: str, far: str | None) -> str:
    role = template_leg.get("tenor_role")
    if role == "near":
        return near
    if role == "far":
        return far or near
    return near


def _mirror_pillar(override: str, template_pillar: str, contract_type: str) -> str:
    """Map a user-picked delta pillar onto a leg.

    Rules :
      - override "atm" → "atm" (always)
      - if template is a call-side wing (``*dc``) → preserve direction, use ``{level}dc``
      - if template is a put-side wing (``*dp``)  → preserve direction, use ``{level}dp``
      - if template is ATM (e.g. straddle leg) → derive direction from the leg's
        contract_type (call → ``{level}dc``, put → ``{level}dp``)

    Why : butterfly's wings are built from 3 *call* contracts (one struck at
    the call-side delta, one struck at the put-side delta). We must preserve
    the template's strike direction, not infer it from contract_type.
    Conversely a Straddle's two ATM legs (1 call + 1 put) need to spread to
    opposite sides on override, derived from contract_type.
    """
    if override == "atm":
        return "atm"
    level = override.replace("dc", "").replace("dp", "").strip()
    if template_pillar.endswith("dc"):
        return f"{level}dc"
    if template_pillar.endswith("dp"):
        return f"{level}dp"
    # Template is ATM → direction comes from the leg's contract_type.
    return f"{level}dc" if contract_type == "call" else f"{level}dp"


def build_structure(
    structure_type: str, near_tenor: str, far_tenor: str | None,
    surface: dict[str, Any],
    *,
    delta_pillar_override: str | None = None,
    strike_override: float | None = None,
    future_contract_size: Literal["full", "micro"] | None = None,
) -> Structure:
    """Build a Structure from a template + market surface.

    ``delta_pillar_override`` and ``strike_override`` apply ONLY to single-leg
    structures (vanilla / future). For multi-leg, the template's per-leg
    pillar is authoritative.
    """
    if structure_type not in TEMPLATES:
        raise ValueError(f"unknown structure: {structure_type}")
    tpl = TEMPLATES[structure_type]
    is_single_leg = len(tpl["legs"]) == 1
    legs: list[Leg] = []
    for i, lt in enumerate(tpl["legs"]):
        actual_tenor = _resolve_tenor(lt, near_tenor, far_tenor)
        # Resolve pillar : if an override is given AND the leg is overridable
        # (default True; False for butterfly body legs), mirror it onto the
        # leg's contract_type. Otherwise keep the template pillar.
        is_overridable = lt.get("overridable", True)
        if delta_pillar_override and is_overridable and lt["contract_type"] != "future":
            pillar = _mirror_pillar(delta_pillar_override, lt["delta_pillar"], lt["contract_type"])
        else:
            pillar = lt["delta_pillar"]
        node = (surface.get(actual_tenor) or {}).get(pillar) or {}
        node_strike = node.get("strike")
        iv = node.get("iv")
        dte = TENOR_TO_DTE.get(actual_tenor, 90)
        expiry = (datetime.now(UTC) + timedelta(days=dte)).date().isoformat()
        is_future = lt["contract_type"] == "future"
        if is_future:
            strike: float | None = None
        elif is_single_leg and strike_override is not None:
            strike = float(strike_override)
        else:
            strike = float(node_strike) if isinstance(node_strike, (int, float)) else None
        legs.append(Leg(
            leg_idx=i,
            contract_type=lt["contract_type"],
            tenor=actual_tenor, expiry=expiry, dte=dte,
            strike=strike,
            qty_factor=int(lt["qty_factor"]), side=lt["side"],
            entry_iv_pct=None if is_future else (float(iv) * 100.0 if isinstance(iv, (int, float)) else None),
        ))
    # Only meaningful for future_buy / future_sell; defaulted to 'full'
    # (6E) when caller didn't specify but the structure is a future.
    is_future_struct = structure_type in ("future_buy", "future_sell")
    fcs: Literal["full", "micro"] | None = None
    if is_future_struct:
        fcs = future_contract_size or "full"
    # Symbol hint for the 6E / M6E split on futures. None for options ;
    # the helper falls back to the structure_type mapping.
    _sym_hint = "M6E" if fcs == "micro" else ("6E" if is_future_struct else None)
    return Structure(
        type=structure_type, reference_tenor=near_tenor, tenor_far=far_tenor,
        legs=legs,
        requires_delta_hedge=tpl["requires_delta_hedge"],
        vega_sign=tpl["vega_sign"],
        future_contract_size=fcs,
        product_label=product_label_from_symbol(_sym_hint, structure_type),
    )


# ────────────────────────────────────────────────────────────────
# Pricing + greeks aggregation (operates on Structure)
# ────────────────────────────────────────────────────────────────


CONTRACT_MULTIPLIER = 1.0  # FX vanilla : per unit of notional, scaling done by qty


@dataclass(frozen=True)
class PricingResult:
    leg_prices_usd: list[float]
    total_premium_usd: float                  # sign : + = paid (long), - = received
    breakeven_pips_each_side: float | None
    max_loss_usd: float
    max_loss_at_expiry_only: bool


@dataclass(frozen=True)
class NetGreeks:
    vega_usd_per_volpt: float
    gamma_usd_per_pip2: float
    theta_usd_per_day: float
    delta_unhedged: float
    delta_post_hedge: float


def _spot_from_surface(surface: dict[str, Any]) -> float:
    """Best-effort spot extraction. Surface stores per-tenor strike at ATM ≈ forward."""
    for t in ("1M", "2M", "3M"):
        node = (surface.get(t) or {}).get("atm") or {}
        if isinstance(node.get("strike"), (int, float)):
            return float(node["strike"])
    return 1.0


def price_structure(structure: Structure, surface: dict[str, Any]) -> PricingResult:
    spot = _spot_from_surface(surface)
    leg_prices: list[float] = []
    total = 0.0
    for leg in structure.legs:
        if leg.contract_type in ("call", "put") and leg.strike and leg.entry_iv_pct:
            T = leg.dte / 365.0
            sigma = leg.entry_iv_pct / 100.0
            price = bs_price(spot, leg.strike, T, sigma, leg.contract_type) * CONTRACT_MULTIPLIER
            leg_prices.append(price)
            sign = +1 if leg.side == "BUY" else -1
            total += sign * price * leg.qty_factor
        else:
            leg_prices.append(0.0)

    # max_loss : long structure → premium paid is the cap. Short → undefined (cap large).
    max_loss = abs(total) if total > 0 else 1e9   # caller will compare vs capital
    # breakeven (rough) : for straddle = premium / vega per pip of spot
    return PricingResult(
        leg_prices_usd=leg_prices,
        total_premium_usd=round(total, 4),
        breakeven_pips_each_side=None,
        max_loss_usd=round(max_loss, 4),
        max_loss_at_expiry_only=total > 0,
    )


def compute_net_greeks(structure: Structure, surface: dict[str, Any]) -> NetGreeks:
    spot = _spot_from_surface(surface)
    fut_mult = FUTURE_MULTIPLIERS.get(structure.future_contract_size or "full", 125_000)
    vega = gamma = theta = delta = 0.0
    for leg in structure.legs:
        if leg.contract_type == "future":
            # Future leg : delta_usd = ±qty × multiplier × spot.
            # 6E (full) : 125 000 × spot ≈ $147 000 per contract @ 1.175.
            # M6E (micro) :  12 500 × spot ≈ $14 700 per contract.
            sign = +1 if leg.side == "BUY" else -1
            delta += sign * leg.qty_factor * fut_mult * spot
            continue
        if leg.contract_type in ("call", "put") and leg.strike and leg.entry_iv_pct:
            T = leg.dte / 365.0
            sigma = leg.entry_iv_pct / 100.0
            g = bs_greeks(spot, leg.strike, T, sigma, leg.contract_type)
            sign = +1 if leg.side == "BUY" else -1
            mult = sign * leg.qty_factor * EUR_FOP_MULTIPLIER
            # Trader-readable USD units. Conventions :
            #   vega   = $ P&L per +1 vol-pt (1 % IV move)
            #   gamma  = $ delta change per +1 pip spot move = bs_gamma × pip
            #   theta  = $ P&L per +1 day decay
            #   delta  = $ exposure   = bs_delta × spot × notional
            vega += g["vega"] * 0.01 * mult
            gamma += g["gamma"] * 1e-4 * mult
            theta += g["theta"] / 365.0 * mult
            delta += g["delta"] * spot * mult
    delta_post_hedge = 0.0 if structure.requires_delta_hedge else delta
    return NetGreeks(
        vega_usd_per_volpt=round(vega, 4),
        gamma_usd_per_pip2=round(gamma, 6),
        theta_usd_per_day=round(theta, 4),
        delta_unhedged=round(delta, 4),
        delta_post_hedge=round(delta_post_hedge, 4),
    )


# ────────────────────────────────────────────────────────────────
# Scenario engine
# ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScenarioConfig:
    label: str
    spot_move_pct: float
    iv_reprice_volpts: float


DEFAULT_SCENARIOS: list[ScenarioConfig] = [
    ScenarioConfig("favorable", 2.0, +1.0),
    ScenarioConfig("neutral", 0.0, 0.0),
    ScenarioConfig("adverse", 0.5, -1.0),
]


# ────────────────────────────────────────────────────────────────
# Greeks grid — revalue greeks at shocked spot levels (Risk-analysis table)
# ────────────────────────────────────────────────────────────────


DEFAULT_SPOT_MOVES_PCT: list[float] = [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0]
DEFAULT_PNL_SPOT_MOVES_PCT: list[float] = [-2.0, -1.0, 0.0, 1.0, 2.0]
DEFAULT_PNL_IV_MOVES_VOLPTS: list[float] = [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0]


def compute_legs_greeks(
    structure: Structure, surface: dict[str, Any],
) -> list[dict[str, Any]]:
    """Greeks per leg (signed, scaled by qty_factor). For futures, only delta
    is non-zero. Used for the per-leg breakdown table on Panel 3."""
    spot = _spot_from_surface(surface)
    fut_mult = FUTURE_MULTIPLIERS.get(structure.future_contract_size or "full", 125_000)
    rows: list[dict[str, Any]] = []
    for leg in structure.legs:
        vega = gamma = theta = delta = 0.0
        if leg.contract_type == "future":
            sign = +1 if leg.side == "BUY" else -1
            delta = sign * leg.qty_factor * fut_mult * spot
        elif leg.contract_type in ("call", "put") and leg.strike and leg.entry_iv_pct:
            T = leg.dte / 365.0
            sigma = leg.entry_iv_pct / 100.0
            g = bs_greeks(spot, leg.strike, T, sigma, leg.contract_type)
            sign = +1 if leg.side == "BUY" else -1
            mult = sign * leg.qty_factor * EUR_FOP_MULTIPLIER
            vega = g["vega"] * 0.01 * mult       # $ / vol-pt
            gamma = g["gamma"] * 1e-4 * mult     # $ delta per pip spot move
            theta = g["theta"] / 365.0 * mult    # $ / day
            delta = g["delta"] * spot * mult     # $ delta
        rows.append({
            "leg_idx": leg.leg_idx,
            "type": leg.contract_type,
            "strike": leg.strike,
            "side": leg.side,
            "qty_factor": leg.qty_factor,
            "vega": round(vega, 2),
            "gamma": round(gamma, 6),
            "theta": round(theta, 2),
            "delta": round(delta, 4),
        })
    return rows


def compute_pnl_grid(
    structure: Structure, surface: dict[str, Any], greeks: NetGreeks,
    spot_moves_pct: list[float] | None = None,
    iv_moves_volpts: list[float] | None = None,
) -> dict[str, Any]:
    """2D Taylor-approximated P&L grid : rows = ΔS%, cols = ΔIV (volpts).

    For each (ΔS, ΔIV) cell : P&L ≈ ½ γ (ΔS_abs)² + V (ΔIV).
    Δ-leg ignored (structures requiring delta hedge collapse to 0). Theta
    ignored (instantaneous shock). Cell at (0,0) flagged ``is_current``.
    """
    spot_moves = spot_moves_pct or DEFAULT_PNL_SPOT_MOVES_PCT
    iv_moves = iv_moves_volpts or DEFAULT_PNL_IV_MOVES_VOLPTS
    spot = _spot_from_surface(surface)
    grid_rows: list[dict[str, Any]] = []
    for ds_pct in spot_moves:
        ds_abs = spot * (ds_pct / 100.0)
        pnl_gamma = 0.5 * greeks.gamma_usd_per_pip2 * (ds_abs ** 2)
        cells: list[dict[str, Any]] = []
        for div in iv_moves:
            pnl_vega = greeks.vega_usd_per_volpt * div
            pnl_total = pnl_gamma + pnl_vega
            cells.append({
                "div_volpts": div,
                "pnl_usd": round(pnl_total, 0),
                "is_current": ds_pct == 0.0 and div == 0.0,
            })
        grid_rows.append({"ds_pct": ds_pct, "cells": cells})
    return {
        "spot_moves_pct": list(spot_moves),
        "iv_moves_volpts": list(iv_moves),
        "rows": grid_rows,
    }


def compute_greeks_grid(
    structure: Structure, surface: dict[str, Any],
    spot_moves_pct: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Re-evaluate net greeks at each shocked spot. The IV stays at the
    leg's entry_iv_pct (no surface re-shape) — this is a Taylor-style grid
    showing how the greeks move with spot, holding everything else fixed.

    Returns a list of rows, one per spot move percentage. The row at 0%
    has ``is_current=True`` so the UI can highlight it.
    """
    moves = spot_moves_pct or DEFAULT_SPOT_MOVES_PCT
    base_spot = _spot_from_surface(surface)
    fut_mult = FUTURE_MULTIPLIERS.get(structure.future_contract_size or "full", 125_000)
    grid: list[dict[str, Any]] = []
    for pct in moves:
        s_shocked = base_spot * (1.0 + pct / 100.0)
        vega = gamma = theta = delta = 0.0
        for leg in structure.legs:
            if leg.contract_type == "future":
                # Future delta_usd = ±qty × multiplier × spot_shocked.
                sign = +1 if leg.side == "BUY" else -1
                delta += sign * leg.qty_factor * fut_mult * s_shocked
                continue
            if leg.contract_type in ("call", "put") and leg.strike and leg.entry_iv_pct:
                T = leg.dte / 365.0
                sigma = leg.entry_iv_pct / 100.0
                g = bs_greeks(s_shocked, leg.strike, T, sigma, leg.contract_type)
                sign = +1 if leg.side == "BUY" else -1
                mult = sign * leg.qty_factor
                vega += g["vega"] * 0.01 * mult
                gamma += g["gamma"] * mult
                theta += g["theta"] / 365.0 * mult
                delta += g["delta"] * mult
        grid.append({
            "spot_pct": pct,
            "spot": round(s_shocked, 4),
            "vega_usd_per_volpt": round(vega, 2),
            "gamma_usd_per_pip2": round(gamma, 6),
            "theta_usd_per_day": round(theta, 2),
            "delta": round(delta, 3),
            "is_current": pct == 0.0,
        })
    return grid


def simulate_scenarios(
    structure: Structure, surface: dict[str, Any], greeks: NetGreeks,
    grid: list[ScenarioConfig] | None = None,
) -> list[dict[str, Any]]:
    grid = grid or DEFAULT_SCENARIOS
    spot = _spot_from_surface(surface)
    results: list[dict[str, Any]] = []
    for sc in grid:
        spot_move_abs = spot * (sc.spot_move_pct / 100.0)
        pnl_gamma = 0.5 * greeks.gamma_usd_per_pip2 * (spot_move_abs ** 2)
        pnl_theta = greeks.theta_usd_per_day * 1  # assume 1 day later
        pnl_vega = greeks.vega_usd_per_volpt * sc.iv_reprice_volpts
        pnl_total = pnl_gamma + pnl_theta + pnl_vega
        results.append({
            "label": sc.label,
            "spot_move_pct": sc.spot_move_pct,
            "iv_reprice_volpts": sc.iv_reprice_volpts,
            "pnl_gamma_theta_usd": round(pnl_gamma + pnl_theta, 2),
            "pnl_vega_usd": round(pnl_vega, 2),
            "pnl_total_usd": round(pnl_total, 2),
        })
    return results


# ────────────────────────────────────────────────────────────────
# Sizer
# ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SizingResult:
    base_qty: int
    multipliers: dict[str, float]
    final_qty_per_leg: int
    leg_quantities: dict[int, int]
    final_premium_usd: float
    sizing_formula: str


def compute_sizing(
    *,
    z_score: float, structure: Structure, total_premium: float,
    book_total_vega_usd: float, book_vega_neutral_threshold: float,
    base_qty: int, threshold_min: float, max_z_multiplier: float,
    book_alpha: float,
    regime: dict[str, Any] | None,
    qty_override: int | None = None,
) -> SizingResult:
    z_factor = min(abs(z_score) / threshold_min, max_z_multiplier) if threshold_min > 0 else 1.0
    if (structure.vega_sign == "positive" and book_total_vega_usd > 0) or (structure.vega_sign == "negative" and book_total_vega_usd < 0):
        ratio = abs(book_total_vega_usd) / max(book_vega_neutral_threshold, 1.0)
        book_penalty = max(0.5, 1.0 - book_alpha * ratio)
    else:
        book_penalty = 1.0
    event_dampener = 0.5 if (regime and regime.get("event_dampener")) else 1.0
    regime_label = (regime or {}).get("regime") or (regime or {}).get("label") or "calm"
    regime_mult = {"calm": 1.0, "stressed": 0.7, "pre_event": 0.0}.get(str(regime_label).lower(), 1.0)

    raw_qty = base_qty * z_factor * book_penalty * event_dampener * regime_mult
    final_qty = max(1, round(raw_qty)) if regime_mult > 0 else 0
    if qty_override is not None and qty_override > 0:
        final_qty = int(qty_override)
    leg_quantities = {leg.leg_idx: final_qty * leg.qty_factor for leg in structure.legs}
    final_premium = total_premium * (final_qty / max(base_qty, 1))
    return SizingResult(
        base_qty=base_qty,
        multipliers={
            "z_score_factor": round(z_factor, 2),
            "book_penalty": round(book_penalty, 2),
            "event_dampener": event_dampener,
            "regime_multiplier": regime_mult,
        },
        final_qty_per_leg=final_qty,
        leg_quantities=leg_quantities,
        final_premium_usd=round(final_premium, 2),
        sizing_formula="base × z_factor × book_penalty × event_dampener × regime_mult",
    )


# ────────────────────────────────────────────────────────────────
# Pre-submit validator (7 checks)
# ────────────────────────────────────────────────────────────────


@dataclass
class Check:
    name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


def run_pre_submit_checks(
    *,
    regime: dict[str, Any] | None,
    armed_z: float, current_z: float, threshold_min: float,
    max_loss_usd: float, capital_total_usd: float, max_loss_pct: float,
    book_total_vega_usd: float, structure_vega_usd: float, max_book_vega_usd: float,
    surface_age_seconds: float, max_iv_age_s: float,
    has_arb_violation: bool,
    min_quoted_size: int, min_liquidity: int,
) -> list[Check]:
    checks: list[Check] = []
    regime_label = (regime or {}).get("regime") or (regime or {}).get("label") or "calm"
    checks.append(Check(
        "regime_not_pre_event", str(regime_label).lower() != "pre_event",
        {"regime": regime_label},
    ))

    z_flipped = (armed_z > 0) != (current_z > 0)
    z_too_weak = abs(current_z) < threshold_min * 0.7
    checks.append(Check(
        "signal_still_actionable", not (z_flipped or z_too_weak),
        {"armed_z": armed_z, "current_z": current_z},
    ))

    # max_loss_under_capital_limit + iv_data_fresh removed by request — the
    # first was too punitive for products where max_loss is uncapped (short
    # vol), the second was redundant with the freshness shown on the YELLOW
    # block. Keep the post-vega book limit which IS a real risk gate.

    post_vega = book_total_vega_usd + structure_vega_usd
    checks.append(Check(
        "vega_under_book_limit", abs(post_vega) <= max_book_vega_usd,
        {"post_trade_vega": round(post_vega, 2), "limit": max_book_vega_usd},
    ))

    checks.append(Check(
        "no_arb_violation_on_legs", not has_arb_violation,
        {},
    ))

    checks.append(Check(
        "minimum_liquidity", min_quoted_size >= min_liquidity,
        {"min_quoted_size": min_quoted_size, "limit": min_liquidity},
    ))
    return checks
