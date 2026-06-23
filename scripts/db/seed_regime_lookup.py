"""Bootstrap ``regime_lookup_table`` from the 15 base patterns.

Each base pattern is in ``(bucket_vol_level, bucket_vol_of_vol, bucket_term_slope)``
form using the legacy 3-bucket alphabet (``-``, ``0``, ``+``). We expand to the
5-bucket alphabet (``--`` ≡ ``-`` and ``++`` ≡ ``+``, intensified) so each
input row produces 1–8 enriched-pattern rows.

Tail-extreme combinations not covered by the 15 mappings fall back to the
seeded ``unmapped_extreme`` row (action = observation only).

Usage :
    PYTHONPATH=src python scripts/db/seed_regime_lookup.py
or, inside the api container :
    docker compose exec api python scripts/db/seed_regime_lookup.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from itertools import product

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# ─────────────────────────────────────────────────────────────────────────
# Base table — 15 regimes (3-bucket alphabet). Source : project conversation.
# ─────────────────────────────────────────────────────────────────────────

BASE_PATTERNS: list[tuple[tuple[str, str, str], int, str, str, str, str]] = [
    # (pattern_3bucket, regime_id, regime_name, family, action_default, asymmetry_note)
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

# 5-bucket expansion : "--" inherits "-" semantics (intensified), "++" inherits "+",
# and "0" stays "0".
EXPANSION: dict[str, list[str]] = {"-": ["--", "-"], "0": ["0"], "+": ["+", "++"]}


def expand_patterns() -> list[dict]:
    """Return the per-pattern dicts ready for INSERT."""
    rows: list[dict] = []
    seen: set[str] = set()
    for (b3, regime_id, regime_name, family, action_default, asymmetry_note) in BASE_PATTERNS:
        bl3, bv3, bs3 = b3
        for bl, bv, bs in product(EXPANSION[bl3], EXPANSION[bv3], EXPANSION[bs3]):
            pattern = f"({bl},{bv},{bs})"
            if pattern in seen:
                continue
            seen.add(pattern)
            intensity = sum(1 for b in (bl, bv, bs) if b in ("--", "++"))
            rows.append({
                "pattern": pattern,
                "regime_id": regime_id,
                "regime_name": regime_name,
                "family": family,
                "action_default": action_default,
                "asymmetry_note": asymmetry_note,
                "intensity_count": intensity,
            })

    # Fallback row — covers the (--, --, --) corner and any 5-bucket combo
    # that isn't reached by any base pattern's expansion.
    rows.append({
        "pattern": "unmapped_extreme",
        "regime_id": 99,
        "regime_name": "unmapped_extreme",
        "family": "Z_fallback",
        "action_default": "observation_only_log_for_review",
        "asymmetry_note": "tail_combination_unseen_in_15_base_regimes",
        "intensity_count": 0,
    })
    return rows


# ─────────────────────────────────────────────────────────────────────────
# DB upsert
# ─────────────────────────────────────────────────────────────────────────

UPSERT_SQL = """
INSERT INTO regime_lookup_table
    (pattern, regime_id, regime_name, family, action_default, asymmetry_note, intensity_count)
VALUES (:pattern, :regime_id, :regime_name, :family, :action_default, :asymmetry_note, :intensity_count)
ON CONFLICT (pattern) DO UPDATE SET
    regime_id = EXCLUDED.regime_id,
    regime_name = EXCLUDED.regime_name,
    family = EXCLUDED.family,
    action_default = EXCLUDED.action_default,
    asymmetry_note = EXCLUDED.asymmetry_note,
    intensity_count = EXCLUDED.intensity_count
"""


async def main() -> None:
    db_url = os.environ.get("DATABASE_URL")
    if db_url is None:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(db_url)
    rows = expand_patterns()
    async with engine.begin() as conn:
        for row in rows:
            await conn.execute(text(UPSERT_SQL), row)
    await engine.dispose()
    print(f"seeded regime_lookup_table : {len(rows)} rows")


if __name__ == "__main__":
    asyncio.run(main())
