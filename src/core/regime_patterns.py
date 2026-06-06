"""Joint-pattern → regime mapping — canonical lookup.

15 base patterns expressed in the 3-bucket alphabet (``-`` / ``0`` /
``+``) are expanded to the 5-bucket alphabet (``--`` / ``-`` / ``0`` /
``+`` / ``++``) so each input row produces 1-8 enriched-pattern rows.
Tail-extreme combinations not covered by any seed fall back to the
``unmapped_extreme`` row whose action defaults to "observation only".

Previously mirrored as the ``regime_pattern_dict`` DB table (seeded by
``scripts/dev/seed_regime_lookup.py`` running against the same
constants below). Migration 039 dropped the table ; this module is
now the single source of truth read by
``api.orchestration.regime_features``.
"""
from __future__ import annotations

from itertools import product
from typing import TypedDict


class RegimePattern(TypedDict):
    pattern: str               # e.g. "(0,0,+)" or "(--,+,++)"
    regime_id: int
    regime_name: str
    family: str                # A_low_vol / B_normal_vol / C_high_vol / Z_fallback
    action_default: str
    asymmetry_note: str | None
    intensity_count: int       # number of "++" or "--" buckets (0..3)


# 15 base regimes in the 3-bucket alphabet ; source : project
# specification §3 (regime taxonomy).
#
# Each tuple : (pattern_3bucket, regime_id, regime_name, family,
#               action_default, asymmetry_note)
_BASE_PATTERNS: list[
    tuple[tuple[str, str, str], int, str, str, str, str]
] = [
    (("-", "0", "+"),  1,  "sleep_deep_steep_contango",       "A_low_vol",   "monitor_complacency",                "long_short_dated_vol_carry_negative"),
    (("-", "0", "0"),  2,  "calm_baseline",                   "A_low_vol",   "no_action",                          "no_signal"),
    (("-", "+", "0"),  3,  "stable_level_agitated",           "A_low_vol",   "long_vega_long_vomma",               "precursor_breakout"),
    (("-", "0", "-"),  4,  "flat_slope_low_level",            "A_low_vol",   "calendar_spread_event_play",         "event_driven_local"),
    (("-", "+", "-"),  5,  "flat_slope_agitated",             "A_low_vol",   "event_play_high_uncertainty",        "volga_exposure"),
    (("0", "0", "0"),  6,  "pure_noise_baseline",             "B_normal_vol","no_action_vol_driven",               "depend_on_other_signals"),
    (("0", "+", "0"),  7,  "hidden_volatility",               "B_normal_vol","relative_value_vol_spreads",         "dispersion_between_tenors"),
    (("0", "0", "+"),  8,  "steep_slope_normal_level",        "B_normal_vol","calendar_spread_long_short",         "convex_decay_with_event"),
    (("0", "0", "-"),  9,  "stress_local_naissant",           "B_normal_vol","size_reduce_monitor",                "transition_to_stressed"),
    (("0", "+", "+"),  10, "cross_product_divergence",        "B_normal_vol","short_short_long_long_hedged_gamma", "terminal_cycle_repricing"),
    (("+", "0", "0"),  11, "elevated_calm",                   "C_high_vol",  "structural_hot_regime",              "new_setpoint_no_divergence"),
    (("+", "+", "0"),  12, "active_stress_normal_slope",      "C_high_vol",  "reduced_sizing",                     "resolution_phase_unstable"),
    (("+", "+", "-"),  13, "full_stress",                     "C_high_vol",  "no_directional_vol_focus_microstructure", "active_crisis"),
    (("+", "0", "-"),  14, "elevated_calm_inverted_curve",    "C_high_vol",  "convergence_trade_patient",          "exit_of_stress"),
    (("+", "+", "+"),  15, "stressed_contango_persists",      "C_high_vol",  "observation_only",                   "pre_crisis_signature"),
]

# 5-bucket expansion : "--" inherits the "-" semantics intensified,
# "++" inherits "+", "0" stays "0".
_EXPANSION: dict[str, list[str]] = {
    "-": ["--", "-"],
    "0": ["0"],
    "+": ["+", "++"],
}


def _build_pattern_dict() -> dict[str, RegimePattern]:
    out: dict[str, RegimePattern] = {}
    for (b3, regime_id, regime_name, family, action_default, asymmetry_note) in _BASE_PATTERNS:
        bl3, bv3, bs3 = b3
        for bl, bv, bs in product(_EXPANSION[bl3], _EXPANSION[bv3], _EXPANSION[bs3]):
            pattern = f"({bl},{bv},{bs})"
            if pattern in out:
                continue
            intensity = sum(b in ("++", "--") for b in (bl, bv, bs))
            out[pattern] = {
                "pattern": pattern,
                "regime_id": regime_id,
                "regime_name": regime_name,
                "family": family,
                "action_default": action_default,
                "asymmetry_note": asymmetry_note,
                "intensity_count": intensity,
            }
    # Catch-all fallback for tail-extreme combinations unseen in the 15
    # base patterns. Action = observation only.
    out["unmapped_extreme"] = {
        "pattern": "unmapped_extreme",
        "regime_id": 99,
        "regime_name": "unmapped_extreme",
        "family": "Z_fallback",
        "action_default": "observation_only_log_for_review",
        "asymmetry_note": "tail_combination_unseen_in_15_base_regimes",
        "intensity_count": 0,
    }
    return out


# Built at module import time — tiny dict (~80 entries), zero
# observable cost.
REGIME_PATTERNS: dict[str, RegimePattern] = _build_pattern_dict()


def lookup_regime(pattern: str) -> RegimePattern:
    """Resolve ``pattern`` to its regime metadata. Returns the
    ``unmapped_extreme`` fallback row when the pattern is not in the
    seeded set — the caller can treat that as "do nothing"."""
    return REGIME_PATTERNS.get(pattern) or REGIME_PATTERNS["unmapped_extreme"]
