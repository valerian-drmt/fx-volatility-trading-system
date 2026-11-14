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

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from core.execution.contract_builder import listed_option_expiry
from core.execution.revalidation import SIGNAL_ACTIONABLE_FACTOR, signal_still_actionable
from core.pricing import bs as _bs
from core.products import product_label_from_symbol
from core.units import EUR_FOP_MULTIPLIER, FUTURE_MULTIPLIERS, PIP_SIZE, VOLPT
from core.vol.tenors import PILLAR_TARGET_DTE, snap_tenor

# 6-tenor canonical grid — the one map in core.vol.tenors (#331).
TENOR_TO_DTE = dict(PILLAR_TARGET_DTE)
DELTA_PILLARS = ("10dp", "25dp", "atm", "25dc", "10dc")


# ────────────────────────────────────────────────────────────────
# Pricing — thin wrappers over the single kernel ``core.pricing.bs`` (#331).
# ``right`` here is the catalog's "call" / "put"; the kernel takes "C" / "P".
# Units are the kernel's raw units (vega per unit sigma, theta per YEAR).
# ────────────────────────────────────────────────────────────────
def _kernel_right(right: str) -> str:
    return "C" if str(right).lower().startswith("c") else "P"


def bs_price(F: float, K: float, T: float, sigma: float, right: str) -> float:
    return _bs.bs_price(F, K, T, sigma, _kernel_right(right))


def bs_greeks(F: float, K: float, T: float, sigma: float, right: str) -> dict[str, float]:
    """delta / gamma / vega / theta in raw (unscaled) units.

    Units :
      delta  : ∂P/∂F             — fraction (call ≈ 0..1)
      gamma  : ∂²P/∂F²           — per unit of F
      vega   : ∂P/∂σ             — per unit of σ (NOT per vol-pt)
      theta  : ∂P/∂t (per year)
    """
    return _bs.bs_greeks(F, K, T, sigma, _kernel_right(right))


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


# No "in-strategy" recommendation set: the desk surfaces indicators, it does not
# advise (see docs/strategy.md). Structure templates remain buildable references,
# never a prescribed catalogue.


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
    # Snap (surface tenor change): when the requested tenor has no listed contract
    # (interpolated), the leg is built at the nearest LISTED tenor instead. The
    # trader's requested tenor is preserved for the ticket; ``snapped`` flags it.
    requested_tenor: str | None = None
    snapped: bool = False


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


# Contract multipliers + pip/vol-point sizes live in ``core.units`` (the
# single source of truth for the USD-at-notional convention) — imported
# above and re-exported here for backwards compatibility.
# IB ``Contract.symbol`` convention : EUR for full size 6E, M6E for micro.
# Cf. core/contracts.py ContractSpec.symbol.
FUTURE_IB_SYMBOLS: dict[str, str] = {"full": "EUR", "micro": "M6E"}
# Trader-friendly label shown in the UI (the IB Contract.symbol is not what
# the operator reads on a chart — "6E" / "M6E" matches the ticker).
FUTURE_DISPLAY_SYMBOLS: dict[str, str] = {"full": "6E", "micro": "M6E"}
# Commission per contract and per side, in USD: the ONE table (#483). The
# ticket showed 2.1 from a browser constant next to the server's 2.0 / 2.40.
COMMISSION_USD: dict[str, float] = {"option": 2.00, "full": 2.40, "micro": 0.60}
FUTURE_COMMISSION_USD: dict[str, float] = {k: COMMISSION_USD[k] for k in ("full", "micro")}


def priced_expiry(target: date, today: date, *, is_option: bool) -> tuple[date, int]:
    """(expiry, days) a leg is priced to. A tenor is a target ("3M" = 90
    days), the order trades the listed contract of that month: an option is
    priced to THAT expiry (#483; a 3M leg was priced on 90 days while its
    contract had 84 left). When the month's option has already expired the
    target is kept: there is nothing better to price to."""
    if is_option:
        listed = listed_option_expiry(target)
        if listed > today:
            return listed, (listed - today).days
    return target, max((target - today).days, 0)


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
    as_of: datetime,
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
        is_future = lt["contract_type"] == "future"
        target = (as_of + timedelta(days=TENOR_TO_DTE.get(actual_tenor, 90))).date()  # the caller's clock (#332)
        expiry_d, dte = priced_expiry(target, as_of.date(), is_option=not is_future)
        expiry = expiry_d.isoformat()
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


def _strangle_delta_bucket(opts: list[Leg], spot: float | None) -> str:
    """Nearest quoted Δ bucket ("10d" / "25d") for a strangle's OTM legs, from
    their BS |delta|. "" when it can't be computed (no spot / no IV)."""
    if spot is None:
        return ""
    ds: list[float] = []
    for leg in opts:
        if leg.strike is None or leg.entry_iv_pct is None:
            continue
        d = bs_greeks(spot, leg.strike, max(leg.dte, 0) / 365.0,
                      leg.entry_iv_pct / 100.0, leg.contract_type)["delta"]
        ds.append(abs(d))
    if not ds:
        return ""
    avg = sum(ds) / len(ds)
    return "10d" if avg < 0.175 else "25d" if avg < 0.375 else ""


def classify_legs(legs: list[Leg], spot: float | None = None) -> str:
    """Name what a free leg-set *expresses* — vocabulary, not prescription
    (cf. docs/strategy.md §4). Returns ``"custom"`` when no common shape matches.

    Pure display: the trader composes freely; this only labels the result so the
    preview can say "this reads as a long strangle" without ever imposing it.
    ``spot`` (optional) lets a strangle carry its Δ bucket → "long strangle 25d".
    """
    opts = [leg for leg in legs if leg.contract_type in ("call", "put")]
    futs = [leg for leg in legs if leg.contract_type == "future"]
    if opts and not futs:
        n = len(opts)
        sides = {leg.side for leg in opts}
        types = {leg.contract_type for leg in opts}
        tenors = {leg.tenor for leg in opts}
        side = "long" if sides == {"BUY"} else "short" if sides == {"SELL"} else None
        if n == 1:
            leg = opts[0]
            return f"{'long' if leg.side == 'BUY' else 'short'} {leg.contract_type}"
        # Calendar : same type, two expiries — regardless of side. A real calendar
        # is OPPOSITE-side (sell near / buy far), so this must come before the
        # side-based branches (which only match same-side structures) or it falls
        # through to "custom".
        if n == 2 and len(types) == 1 and len(tenors) == 2:
            return "calendar"
        if n == 2 and side is not None:
            if types == {"call", "put"}:
                strikes = {leg.strike for leg in opts if leg.strike is not None}
                if len(strikes) <= 1:
                    return f"{side} straddle"
                bucket = _strangle_delta_bucket(opts, spot)
                return f"{side} strangle{(' ' + bucket) if bucket else ''}"
            if len(types) == 1:
                return f"{side} vertical spread"
        if n == 2 and side is None and types == {"call", "put"}:
            # Carry the Δ bucket like the strangle does ("risk reversal 25d"), so
            # the blotter shows the wing the trader actually picked instead of
            # re-bucketing the live strike (which snaps to the wrong pillar). A
            # symmetric RR yields one bucket; an asymmetric one yields none → bare.
            bucket = _strangle_delta_bucket(opts, spot)
            return f"risk reversal{(' ' + bucket) if bucket else ''}"
        # opposite sides, one type, one tenor → a vertical (call/put) spread
        if n == 2 and side is None and len(types) == 1 and len(tenors) == 1:
            return f"{next(iter(types))} spread"
        if n == 3 and len(types) == 1 and len({leg.side for leg in opts}) == 2:
            return "butterfly"
    if futs and not opts:
        return f"{'long' if futs[0].side == 'BUY' else 'short'} future"
    if futs and opts:
        return "custom (options + future)"
    return "custom"


def build_from_legs(
    leg_specs: list[dict[str, Any]],
    surface: dict[str, Any],
    *,
    as_of: datetime,
) -> Structure:
    """Build a *custom* Structure from free, user-composed legs (no template).

    Each spec: ``{contract_type, side, tenor, delta_pillar?, strike?, qty_factor?,
    future_contract_size?}``. The trader picks products/delta/tenor/side freely
    (docs/strategy.md §4) — nothing is imposed. Pricing/greeks are template-
    agnostic, so the resulting Structure flows through the same downstream
    pipeline (price_structure / compute_net_greeks / scenarios / pnl_grid).
    """
    if not leg_specs:
        raise ValueError("at least one leg required")
    legs: list[Leg] = []
    fcs: Literal["full", "micro"] | None = None
    now = as_of  # the caller's clock (#332): core never reads the wall clock
    for i, spec in enumerate(leg_specs):
        ct = spec.get("contract_type")
        if ct not in ("call", "put", "future"):
            raise ValueError(f"leg {i}: bad contract_type {ct!r}")
        side = str(spec.get("side") or "").upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"leg {i}: bad side {side!r}")
        tenor = str(spec.get("tenor") or "3M").upper()
        if tenor not in TENOR_TO_DTE:
            raise ValueError(f"leg {i}: unknown tenor {tenor!r}")
        qf_raw = spec.get("qty_factor")
        qf = int(qf_raw if qf_raw is not None else 1)
        if qf <= 0:
            raise ValueError(f"leg {i}: qty_factor must be > 0")
        dte = TENOR_TO_DTE[tenor]
        expiry = (now + timedelta(days=dte)).date().isoformat()
        if ct == "future":
            size = spec.get("future_contract_size") or "full"
            if size not in ("full", "micro"):
                raise ValueError(f"leg {i}: bad future_contract_size {size!r}")
            fcs = fcs or size  # type: ignore[assignment]
            legs.append(Leg(
                leg_idx=i, contract_type="future", tenor=tenor, expiry=expiry,
                dte=dte, strike=None, qty_factor=qf, side=side, entry_iv_pct=None,
            ))
            continue
        pillar = str(spec.get("delta_pillar") or "atm").lower()
        if pillar not in DELTA_PILLARS:
            raise ValueError(f"leg {i}: bad delta_pillar {pillar!r}")
        # Snap an interpolated/absent tenor to the nearest LISTED tenor — that's
        # the contract the order actually trades. The requested tenor is kept on
        # the leg for the ticket. Surfaces without source flags (synthetic
        # sandbox) treat every present tenor as listed → no snap.
        eff_tenor, snapped = snap_tenor(tenor, surface)
        eff_target = (now + timedelta(days=TENOR_TO_DTE.get(eff_tenor, dte))).date()
        eff_expiry_d, eff_dte = priced_expiry(eff_target, now.date(), is_option=True)
        eff_expiry = eff_expiry_d.isoformat()
        node = (surface.get(eff_tenor) or {}).get(pillar) or {}
        strike_override = spec.get("strike")
        node_strike = node.get("strike")
        iv = node.get("iv")
        if strike_override is not None:
            strike: float | None = float(strike_override)
            # the vol of the smile AT that strike, not the pillar's
            smile_iv = iv_at_strike(surface, eff_tenor, float(strike_override))
            iv = smile_iv if smile_iv is not None else iv
        else:
            strike = float(node_strike) if isinstance(node_strike, (int, float)) else None
        legs.append(Leg(
            leg_idx=i, contract_type=ct, tenor=eff_tenor, expiry=eff_expiry, dte=eff_dte,
            strike=strike, qty_factor=qf, side=side,
            entry_iv_pct=float(iv) * 100.0 if isinstance(iv, (int, float)) else None,
            requested_tenor=tenor if snapped else None, snapped=snapped,
        ))
    has_option = any(leg.contract_type in ("call", "put") for leg in legs)
    # Net vega sign (display hint): bought options add vega, sold subtract.
    vega_score = sum(
        (1 if leg.side == "BUY" else -1) * leg.qty_factor
        for leg in legs if leg.contract_type in ("call", "put")
    )
    vega_sign = "positive" if vega_score > 0 else "negative" if vega_score < 0 else "neutral"
    distinct_tenors = sorted({leg.tenor for leg in legs}, key=lambda t: TENOR_TO_DTE[t])
    return Structure(
        type="custom",
        reference_tenor=legs[0].tenor,
        tenor_far=distinct_tenors[-1] if len(distinct_tenors) > 1 else None,
        legs=legs,
        # Futures-only = the future *is* the delta (no hedge). Any option leg
        # ⇒ delta-hedgeable, matching the single-leg vanilla/future templates.
        requires_delta_hedge=has_option,
        vega_sign=vega_sign,
        future_contract_size=fcs,
        product_label=classify_legs(legs, _spot_from_surface(surface)),
    )


# ────────────────────────────────────────────────────────────────
# Pricing + greeks aggregation (operates on Structure)
# ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PricingResult:
    """USD-at-notional pricing (convention : ``core.units``).

    ``leg_prices_usd`` = per-contract premium in real USD
    (``bs_price × EUR_FOP_MULTIPLIER``). Callers that need IB's raw
    price-points unit (lmtPrice) divide back by ``EUR_FOP_MULTIPLIER``.
    """

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


def forward_for_tenor(surface: dict[str, Any], tenor: str | None) -> float:
    """The forward of ONE tenor: its own at-the-money strike. The 1M forward
    used to price every tenor; on a 3-month option that moves the price and
    the delta by the forward points the surface already carries (#483)."""
    node = (surface.get(tenor or "") or {}).get("atm") or {}
    if isinstance(node.get("strike"), (int, float)) and float(node["strike"]) > 0:
        return float(node["strike"])
    return _spot_from_surface(surface)


def iv_at_strike(surface: dict[str, Any], tenor: str | None, strike: float) -> float | None:
    """The vol of a tenor's smile AT a strike: linear in strike between the
    pillars the surface holds, flat beyond the wings. None when the tenor has
    no pillar with both a strike and a vol.

    A strike typed on the ticket used to be priced at the at-the-money vol,
    whatever its distance from the money: a wing was priced without its skew
    (#483)."""
    nodes = surface.get(tenor or "") or {}
    points = sorted(
        (float(n["strike"]), float(n["iv"]))
        for n in (nodes.get(p) or {} for p in DELTA_PILLARS)
        if isinstance(n.get("strike"), (int, float)) and isinstance(n.get("iv"), (int, float))
    )
    if not points:
        return None
    if strike <= points[0][0]:
        return points[0][1]
    if strike >= points[-1][0]:
        return points[-1][1]
    for (k0, v0), (k1, v1) in itertools.pairwise(points):
        if k0 <= strike <= k1:
            return v0 if k1 == k0 else v0 + (v1 - v0) * (strike - k0) / (k1 - k0)
    return points[-1][1]


def price_structure(structure: Structure, surface: dict[str, Any]) -> PricingResult:
    leg_prices: list[float] = []
    total = 0.0
    for leg in structure.legs:
        if leg.contract_type in ("call", "put") and leg.strike and leg.entry_iv_pct:
            T = leg.dte / 365.0
            sigma = leg.entry_iv_pct / 100.0
            # bs_price is in price points ; × EUR_FOP_MULTIPLIER = real USD
            # per contract (cf. core.units). Priced on the leg's own forward.
            spot = forward_for_tenor(surface, leg.tenor)
            price = bs_price(spot, leg.strike, T, sigma, leg.contract_type) * EUR_FOP_MULTIPLIER
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


def _leg_greeks(leg: Leg, spot: float, fut_mult: float) -> dict[str, float]:
    """Signed USD greeks for ONE leg — the single place where the
    ``core.units`` convention is applied inside this module :

      vega  = bs_vega  × VOLPT × mult        ($ per +1 vol-pt)
      gamma = bs_gamma × PIP_SIZE² × mult    ($ per pip² — gamma P&L is
                                              0.5 × gamma × ΔS_pips²)
      theta = bs_theta_per_year / 365 × mult ($ per day)
      delta = bs_delta × spot × mult         ($ notional exposure)

    with ``mult = ±qty_factor × EUR_FOP_MULTIPLIER``. A future leg carries
    delta only (± qty × future multiplier × spot).
    """
    vega = gamma = theta = delta = 0.0
    if leg.contract_type == "future":
        # 6E (full) : 125 000 × spot ≈ $147 000 per contract @ 1.175.
        # M6E (micro) :  12 500 × spot ≈ $14 700 per contract.
        sign = +1 if leg.side == "BUY" else -1
        delta = sign * leg.qty_factor * fut_mult * spot
    elif leg.contract_type in ("call", "put") and leg.strike and leg.entry_iv_pct:
        T = leg.dte / 365.0
        sigma = leg.entry_iv_pct / 100.0
        g = bs_greeks(spot, leg.strike, T, sigma, leg.contract_type)
        sign = +1 if leg.side == "BUY" else -1
        mult = sign * leg.qty_factor * EUR_FOP_MULTIPLIER
        vega = g["vega"] * VOLPT * mult
        gamma = g["gamma"] * PIP_SIZE ** 2 * mult
        theta = g["theta"] / 365.0 * mult
        delta = g["delta"] * spot * mult
    return {"vega": vega, "gamma": gamma, "theta": theta, "delta": delta}


def compute_net_greeks(structure: Structure, surface: dict[str, Any]) -> NetGreeks:
    fut_mult = FUTURE_MULTIPLIERS.get(structure.future_contract_size or "full", 125_000)
    vega = gamma = theta = delta = 0.0
    for leg in structure.legs:
        # each leg on the forward of its own tenor
        g = _leg_greeks(leg, forward_for_tenor(surface, leg.tenor), fut_mult)
        vega += g["vega"]
        gamma += g["gamma"]
        theta += g["theta"]
        delta += g["delta"]
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
        g = _leg_greeks(leg, spot, fut_mult)   # core.units convention
        rows.append({
            "leg_idx": leg.leg_idx,
            "type": leg.contract_type,
            "strike": leg.strike,
            "side": leg.side,
            "qty_factor": leg.qty_factor,
            "vega": round(g["vega"], 2),
            "gamma": round(g["gamma"], 6),
            "theta": round(g["theta"], 2),
            "delta": round(g["delta"], 4),
        })
    return rows


def compute_pnl_grid(
    structure: Structure, surface: dict[str, Any], greeks: NetGreeks,
    spot_moves_pct: list[float] | None = None,
    iv_moves_volpts: list[float] | None = None,
) -> dict[str, Any]:
    """2D Taylor-approximated P&L grid : rows = ΔS%, cols = ΔIV (volpts).

    For each (ΔS, ΔIV) cell : P&L ≈ ½ γ (ΔS_pips)² + V (ΔIV) — the spot
    shock is converted to PIPS because gamma is $/pip² (cf. core.units).
    Δ-leg ignored (structures requiring delta hedge collapse to 0). Theta
    ignored (instantaneous shock). Cell at (0,0) flagged ``is_current``.
    """
    spot_moves = spot_moves_pct or DEFAULT_PNL_SPOT_MOVES_PCT
    iv_moves = iv_moves_volpts or DEFAULT_PNL_IV_MOVES_VOLPTS
    spot = _spot_from_surface(surface)
    grid_rows: list[dict[str, Any]] = []
    for ds_pct in spot_moves:
        ds_pips = spot * (ds_pct / 100.0) / PIP_SIZE
        pnl_gamma = 0.5 * greeks.gamma_usd_per_pip2 * (ds_pips ** 2)
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


def simulate_scenarios(
    structure: Structure, surface: dict[str, Any], greeks: NetGreeks,
    grid: list[ScenarioConfig] | None = None,
) -> list[dict[str, Any]]:
    grid = grid or DEFAULT_SCENARIOS
    spot = _spot_from_surface(surface)
    results: list[dict[str, Any]] = []
    for sc in grid:
        # gamma is $/pip² (core.units) → shock expressed in pips.
        spot_move_pips = spot * (sc.spot_move_pct / 100.0) / PIP_SIZE
        pnl_gamma = 0.5 * greeks.gamma_usd_per_pip2 * (spot_move_pips ** 2)
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
    # ``total_premium`` is the premium of ONE unit of the structure. It was
    # scaled by final/base, which is 1 on a manual ticket: a 22-lot showed
    # and was checked on the premium of one contract (#483).
    final_premium = total_premium * final_qty
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
    """One pre-submit check. ``passed`` is None when the check could not be
    evaluated: it is then reported as not evaluated, never as passed (#483)."""
    name: str
    passed: bool | None
    details: dict[str, Any] = field(default_factory=dict)
    blocking: bool = True

    @property
    def blocks(self) -> bool:
        return self.blocking and self.passed is False


def run_pre_submit_checks(
    *,
    regime: dict[str, Any] | None,
    armed_z: float, current_z: float, threshold_min: float,
    butterfly_arb_free: bool | None = None, calendar_arb_free: bool | None = None,
) -> list[Check]:
    """``*_arb_free`` are the flags of the surface's SSVI fit; None when the
    surface carries no fit."""
    flags = [f for f in (butterfly_arb_free, calendar_arb_free) if f is not None]
    arb_free: bool | None = all(flags) if flags else None
    checks: list[Check] = []
    regime_label = (regime or {}).get("regime") or (regime or {}).get("label") or "calm"
    checks.append(Check(
        "regime_not_pre_event", str(regime_label).lower() != "pre_event",
        {"regime": regime_label},
    ))

    # Same rule as core.execution.revalidation (#314): the factor lives there.
    checks.append(Check(
        "signal_still_actionable", signal_still_actionable(armed_z, current_z, threshold_min),
        {"armed_z": armed_z, "current_z": current_z, "threshold": threshold_min * SIGNAL_ACTIONABLE_FACTOR},
    ))

    # What blocks a preview is what the submit refuses (#483): the regime and
    # the signal above, then the gate's own verdict (``submit_blockers``).
    # The two checks below INFORM. No limit computed by the code blocks an
    # order (owner rule): the vega check against ``max_book_vega_usd`` is
    # gone, the trader's own vega limit is judged by the gate.
    checks.append(Check(
        "no_arb_violation_on_legs", arb_free,
        {"butterfly_arb_free": butterfly_arb_free, "calendar_arb_free": calendar_arb_free},
        blocking=False,
    ))
    # No quoted size reaches the preview: not evaluated, not "passed".
    checks.append(Check("minimum_liquidity", None, {"reason": "no quoted size available"}, blocking=False))
    return checks
