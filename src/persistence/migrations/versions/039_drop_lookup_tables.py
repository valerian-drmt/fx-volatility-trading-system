"""Drop 3 lookup tables — collapse onto Python source-of-truth modules.

Tables dropped (same anti-pattern as ``vrp_default_curve`` resolved in
migration 038) :

  - ``regime_pattern_dict``         → ``core.regime_patterns.REGIME_PATTERNS``
  - ``pca_structure_recommendation`` → ``core.pca_recommendations.PCA_RECOMMENDATIONS``
  - ``structure_definition_ref``    → ``core.trade_preview.TEMPLATES`` (entries
                                       with ``in_catalog=True``)

All three were :

  * static (rows changed only via a code-side seed update + an alembic
    migration to re-INSERT, never edited live)
  * trivially small (6 to ~80 rows each)
  * accessed via a single SELECT then dict-comprehended into a Python
    lookup at every cycle

…i.e. a Python dict in disguise. Moving them out of the DB removes a
"two sources of truth that must stay in sync" trap, eliminates a round-
trip per cycle for ``pca_structure_recommendation`` / ``regime_pattern_dict``,
and makes the catalog editable in the same PR as the trade logic that
consumes it.

Downgrade re-creates the three tables and re-seeds them from literal
row snapshots frozen in this file (taken from the canonical Python
dicts at migration time) so rollback is loss-less. Migrations must
never import live ``core`` modules — ``core.pca_recommendations`` was
later deleted, which used to break every downgrade past this revision.

Revision ID: 039_drop_lookup_tables
Revises: 038_drop_vrp_default_curve
Create Date: 2026-06-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "039_drop_lookup_tables"
down_revision: str | None = "038_drop_vrp_default_curve"
branch_labels: str | None = None
depends_on: str | None = None

JSONB_PORTABLE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.drop_table("regime_pattern_dict")
    op.drop_table("pca_structure_recommendation")
    op.drop_table("structure_definition_ref")


def downgrade() -> None:
    # ── regime_pattern_dict ──
    op.create_table(
        "regime_pattern_dict",
        sa.Column("pattern", sa.String(length=20), nullable=False),
        sa.Column("regime_id", sa.Integer(), nullable=False),
        sa.Column("regime_name", sa.String(length=60), nullable=False),
        sa.Column("family", sa.String(length=40), nullable=False),
        sa.Column("action_default", sa.String(length=80), nullable=False),
        sa.Column("asymmetry_note", sa.String(length=120), nullable=True),
        sa.Column("intensity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("pattern"),
    )
    op.bulk_insert(
        sa.table(
            "regime_pattern_dict",
            sa.column("pattern", sa.String),
            sa.column("regime_id", sa.Integer),
            sa.column("regime_name", sa.String),
            sa.column("family", sa.String),
            sa.column("action_default", sa.String),
            sa.column("asymmetry_note", sa.String),
            sa.column("intensity_count", sa.Integer),
        ),
        _REGIME_PATTERN_ROWS,
    )

    # ── pca_structure_recommendation ──
    op.create_table(
        "pca_structure_recommendation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pc_id", sa.Integer(), nullable=False),
        sa.Column("signal_label", sa.String(length=15), nullable=False),
        sa.Column("recommended_structure", sa.String(length=60), nullable=False),
        sa.Column("default_tenor", sa.String(length=10), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=True),
        sa.Column("rationale", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pc_id", "signal_label", "is_active",
                            name="uq_signal_rec_map_pc_label_active"),
        sa.CheckConstraint("signal_label IN ('CHEAP','EXPENSIVE')",
                           name="ck_signal_rec_map_label"),
    )
    op.bulk_insert(
        sa.table(
            "pca_structure_recommendation",
            sa.column("pc_id", sa.Integer),
            sa.column("signal_label", sa.String),
            sa.column("recommended_structure", sa.String),
            sa.column("default_tenor", sa.String),
            sa.column("description", sa.String),
            sa.column("rationale", sa.String),
        ),
        _PCA_RECOMMENDATION_ROWS,
    )

    # ── structure_definition_ref ──
    op.create_table(
        "structure_definition_ref",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("structure_type", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=80), nullable=False),
        sa.Column("leg_template", JSONB_PORTABLE, nullable=False),
        sa.Column("min_legs", sa.Integer(), nullable=False),
        sa.Column("max_legs", sa.Integer(), nullable=False),
        sa.Column("requires_delta_hedge", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("typical_vega_sign", sa.String(length=10), nullable=False),
        sa.Column("typical_gamma_sign", sa.String(length=10), nullable=False),
        sa.Column("typical_theta_sign", sa.String(length=10), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column("rationale_for_pc", sa.String(length=300), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("structure_type"),
    )
    op.bulk_insert(
        sa.table(
            "structure_definition_ref",
            sa.column("structure_type", sa.String),
            sa.column("display_name", sa.String),
            sa.column("leg_template", JSONB_PORTABLE),
            sa.column("min_legs", sa.Integer),
            sa.column("max_legs", sa.Integer),
            sa.column("requires_delta_hedge", sa.Boolean),
            sa.column("typical_vega_sign", sa.String),
            sa.column("typical_gamma_sign", sa.String),
            sa.column("typical_theta_sign", sa.String),
            sa.column("description", sa.String),
            sa.column("rationale_for_pc", sa.String),
        ),
        _STRUCTURE_CATALOG_ROWS,
    )


# ── Seed-row snapshots (frozen literals — never import live core modules) ──

# core.pca_recommendations.PCA_RECOMMENDATIONS as of this revision
# (module deleted later — reconstructed from git history).
_PCA_RECOMMENDATION_ROWS: list[dict] = [
    {
        "pc_id": 1, "signal_label": "CHEAP",
        "recommended_structure": "straddle_atm",
        "default_tenor": "3M",
        "description": "Long straddle ATM",
        "rationale": "PC1 CHEAP = vol level low → buy vol via ATM straddle",
    },
    {
        "pc_id": 1, "signal_label": "EXPENSIVE",
        "recommended_structure": "short_strangle",
        "default_tenor": "3M",
        "description": "Short OTM strangle",
        "rationale": "PC1 EXPENSIVE = vol level high → sell vol via OTM strangle",
    },
    {
        "pc_id": 2, "signal_label": "CHEAP",
        "recommended_structure": "calendar_long",
        "default_tenor": "1M_3M",
        "description": "Calendar buying long tenor",
        "rationale": "PC2 CHEAP = term slope inverted → buy long tenor",
    },
    {
        "pc_id": 2, "signal_label": "EXPENSIVE",
        "recommended_structure": "calendar_short",
        "default_tenor": "1M_3M",
        "description": "Calendar selling long tenor",
        "rationale": "PC2 EXPENSIVE = term slope steep → sell long tenor",
    },
    {
        "pc_id": 3, "signal_label": "CHEAP",
        "recommended_structure": "long_butterfly_25d",
        "default_tenor": "3M",
        "description": "Long 25d butterfly",
        "rationale": "PC3 CHEAP = wings cheap relative to ATM",
    },
    {
        "pc_id": 3, "signal_label": "EXPENSIVE",
        "recommended_structure": "short_butterfly_25d",
        "default_tenor": "3M",
        "description": "Short 25d butterfly",
        "rationale": "PC3 EXPENSIVE = wings rich relative to ATM",
    },
]

# core.trade_preview.TEMPLATES entries with in_catalog=True, expanded to
# the structure_definition_ref column shape.
_STRUCTURE_CATALOG_ROWS: list[dict] = [
    {
        "structure_type": "straddle_atm",
        "display_name": "Long straddle ATM",
        "leg_template": [
            {"contract_type": "call", "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
            {"contract_type": "put", "delta_pillar": "atm", "side": "BUY", "qty_factor": 1},
        ],
        "min_legs": 2,
        "max_legs": 2,
        "requires_delta_hedge": True,
        "typical_vega_sign": "positive",
        "typical_gamma_sign": "positive",
        "typical_theta_sign": "negative",
        "description": "Buy ATM call + ATM put",
        "rationale_for_pc": "PC1 CHEAP : level low → buy vol",
    },
    {
        "structure_type": "short_strangle",
        "display_name": "Short OTM strangle",
        "leg_template": [
            {"contract_type": "call", "delta_pillar": "25dc", "side": "SELL", "qty_factor": 1},
            {"contract_type": "put", "delta_pillar": "25dp", "side": "SELL", "qty_factor": 1},
        ],
        "min_legs": 2,
        "max_legs": 2,
        "requires_delta_hedge": True,
        "typical_vega_sign": "negative",
        "typical_gamma_sign": "negative",
        "typical_theta_sign": "positive",
        "description": "Sell 25d strangle",
        "rationale_for_pc": "PC1 EXPENSIVE : level high → sell vol",
    },
    {
        "structure_type": "calendar_long",
        "display_name": "Calendar buy long-dated",
        "leg_template": [
            {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "near",
             "side": "SELL", "qty_factor": 1},
            {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "far",
             "side": "BUY", "qty_factor": 1},
        ],
        "min_legs": 2,
        "max_legs": 2,
        "requires_delta_hedge": True,
        "typical_vega_sign": "positive",
        "typical_gamma_sign": "neutral",
        "typical_theta_sign": "neutral",
        "description": "Sell near, buy far",
        "rationale_for_pc": "PC2 CHEAP : term inverted",
    },
    {
        "structure_type": "calendar_short",
        "display_name": "Calendar sell long-dated",
        "leg_template": [
            {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "near",
             "side": "BUY", "qty_factor": 1},
            {"contract_type": "call", "delta_pillar": "atm", "tenor_role": "far",
             "side": "SELL", "qty_factor": 1},
        ],
        "min_legs": 2,
        "max_legs": 2,
        "requires_delta_hedge": True,
        "typical_vega_sign": "negative",
        "typical_gamma_sign": "neutral",
        "typical_theta_sign": "neutral",
        "description": "Buy near, sell far",
        "rationale_for_pc": "PC2 EXPENSIVE : term steep",
    },
    {
        "structure_type": "long_butterfly_25d",
        "display_name": "Long butterfly (10d wings)",
        "leg_template": [
            {"contract_type": "call", "delta_pillar": "10dc", "side": "BUY",
             "qty_factor": 1, "overridable": True},
            {"contract_type": "call", "delta_pillar": "atm", "side": "SELL",
             "qty_factor": 2, "overridable": False},
            {"contract_type": "call", "delta_pillar": "10dp", "side": "BUY",
             "qty_factor": 1, "overridable": True},
        ],
        "min_legs": 3,
        "max_legs": 3,
        "requires_delta_hedge": True,
        "typical_vega_sign": "neutral",
        "typical_gamma_sign": "neutral",
        "typical_theta_sign": "neutral",
        "description": "Long wings, short body",
        "rationale_for_pc": "PC3 CHEAP : wings cheap",
    },
    {
        "structure_type": "short_butterfly_25d",
        "display_name": "Short butterfly (10d wings)",
        "leg_template": [
            {"contract_type": "call", "delta_pillar": "10dc", "side": "SELL",
             "qty_factor": 1, "overridable": True},
            {"contract_type": "call", "delta_pillar": "atm", "side": "BUY",
             "qty_factor": 2, "overridable": False},
            {"contract_type": "call", "delta_pillar": "10dp", "side": "SELL",
             "qty_factor": 1, "overridable": True},
        ],
        "min_legs": 3,
        "max_legs": 3,
        "requires_delta_hedge": True,
        "typical_vega_sign": "neutral",
        "typical_gamma_sign": "neutral",
        "typical_theta_sign": "neutral",
        "description": "Short wings, long body",
        "rationale_for_pc": "PC3 EXPENSIVE : wings rich",
    },
]

# core.regime_patterns.REGIME_PATTERNS values (5-bucket expansion of the
# 15 base regimes + the unmapped_extreme fallback), 60 rows.
_REGIME_PATTERN_ROWS: list[dict] = [
    {"pattern": "(--,0,+)", "regime_id": 1, "regime_name": "sleep_deep_steep_contango",
     "family": "A_low_vol", "action_default": "monitor_complacency",
     "asymmetry_note": "long_short_dated_vol_carry_negative", "intensity_count": 1},
    {"pattern": "(--,0,++)", "regime_id": 1, "regime_name": "sleep_deep_steep_contango",
     "family": "A_low_vol", "action_default": "monitor_complacency",
     "asymmetry_note": "long_short_dated_vol_carry_negative", "intensity_count": 2},
    {"pattern": "(-,0,+)", "regime_id": 1, "regime_name": "sleep_deep_steep_contango",
     "family": "A_low_vol", "action_default": "monitor_complacency",
     "asymmetry_note": "long_short_dated_vol_carry_negative", "intensity_count": 0},
    {"pattern": "(-,0,++)", "regime_id": 1, "regime_name": "sleep_deep_steep_contango",
     "family": "A_low_vol", "action_default": "monitor_complacency",
     "asymmetry_note": "long_short_dated_vol_carry_negative", "intensity_count": 1},
    {"pattern": "(--,0,0)", "regime_id": 2, "regime_name": "calm_baseline",
     "family": "A_low_vol", "action_default": "no_action",
     "asymmetry_note": "no_signal", "intensity_count": 1},
    {"pattern": "(-,0,0)", "regime_id": 2, "regime_name": "calm_baseline",
     "family": "A_low_vol", "action_default": "no_action",
     "asymmetry_note": "no_signal", "intensity_count": 0},
    {"pattern": "(--,+,0)", "regime_id": 3, "regime_name": "stable_level_agitated",
     "family": "A_low_vol", "action_default": "long_vega_long_vomma",
     "asymmetry_note": "precursor_breakout", "intensity_count": 1},
    {"pattern": "(--,++,0)", "regime_id": 3, "regime_name": "stable_level_agitated",
     "family": "A_low_vol", "action_default": "long_vega_long_vomma",
     "asymmetry_note": "precursor_breakout", "intensity_count": 2},
    {"pattern": "(-,+,0)", "regime_id": 3, "regime_name": "stable_level_agitated",
     "family": "A_low_vol", "action_default": "long_vega_long_vomma",
     "asymmetry_note": "precursor_breakout", "intensity_count": 0},
    {"pattern": "(-,++,0)", "regime_id": 3, "regime_name": "stable_level_agitated",
     "family": "A_low_vol", "action_default": "long_vega_long_vomma",
     "asymmetry_note": "precursor_breakout", "intensity_count": 1},
    {"pattern": "(--,0,--)", "regime_id": 4, "regime_name": "flat_slope_low_level",
     "family": "A_low_vol", "action_default": "calendar_spread_event_play",
     "asymmetry_note": "event_driven_local", "intensity_count": 2},
    {"pattern": "(--,0,-)", "regime_id": 4, "regime_name": "flat_slope_low_level",
     "family": "A_low_vol", "action_default": "calendar_spread_event_play",
     "asymmetry_note": "event_driven_local", "intensity_count": 1},
    {"pattern": "(-,0,--)", "regime_id": 4, "regime_name": "flat_slope_low_level",
     "family": "A_low_vol", "action_default": "calendar_spread_event_play",
     "asymmetry_note": "event_driven_local", "intensity_count": 1},
    {"pattern": "(-,0,-)", "regime_id": 4, "regime_name": "flat_slope_low_level",
     "family": "A_low_vol", "action_default": "calendar_spread_event_play",
     "asymmetry_note": "event_driven_local", "intensity_count": 0},
    {"pattern": "(--,+,--)", "regime_id": 5, "regime_name": "flat_slope_agitated",
     "family": "A_low_vol", "action_default": "event_play_high_uncertainty",
     "asymmetry_note": "volga_exposure", "intensity_count": 2},
    {"pattern": "(--,+,-)", "regime_id": 5, "regime_name": "flat_slope_agitated",
     "family": "A_low_vol", "action_default": "event_play_high_uncertainty",
     "asymmetry_note": "volga_exposure", "intensity_count": 1},
    {"pattern": "(--,++,--)", "regime_id": 5, "regime_name": "flat_slope_agitated",
     "family": "A_low_vol", "action_default": "event_play_high_uncertainty",
     "asymmetry_note": "volga_exposure", "intensity_count": 3},
    {"pattern": "(--,++,-)", "regime_id": 5, "regime_name": "flat_slope_agitated",
     "family": "A_low_vol", "action_default": "event_play_high_uncertainty",
     "asymmetry_note": "volga_exposure", "intensity_count": 2},
    {"pattern": "(-,+,--)", "regime_id": 5, "regime_name": "flat_slope_agitated",
     "family": "A_low_vol", "action_default": "event_play_high_uncertainty",
     "asymmetry_note": "volga_exposure", "intensity_count": 1},
    {"pattern": "(-,+,-)", "regime_id": 5, "regime_name": "flat_slope_agitated",
     "family": "A_low_vol", "action_default": "event_play_high_uncertainty",
     "asymmetry_note": "volga_exposure", "intensity_count": 0},
    {"pattern": "(-,++,--)", "regime_id": 5, "regime_name": "flat_slope_agitated",
     "family": "A_low_vol", "action_default": "event_play_high_uncertainty",
     "asymmetry_note": "volga_exposure", "intensity_count": 2},
    {"pattern": "(-,++,-)", "regime_id": 5, "regime_name": "flat_slope_agitated",
     "family": "A_low_vol", "action_default": "event_play_high_uncertainty",
     "asymmetry_note": "volga_exposure", "intensity_count": 1},
    {"pattern": "(0,0,0)", "regime_id": 6, "regime_name": "pure_noise_baseline",
     "family": "B_normal_vol", "action_default": "no_action_vol_driven",
     "asymmetry_note": "depend_on_other_signals", "intensity_count": 0},
    {"pattern": "(0,+,0)", "regime_id": 7, "regime_name": "hidden_volatility",
     "family": "B_normal_vol", "action_default": "relative_value_vol_spreads",
     "asymmetry_note": "dispersion_between_tenors", "intensity_count": 0},
    {"pattern": "(0,++,0)", "regime_id": 7, "regime_name": "hidden_volatility",
     "family": "B_normal_vol", "action_default": "relative_value_vol_spreads",
     "asymmetry_note": "dispersion_between_tenors", "intensity_count": 1},
    {"pattern": "(0,0,+)", "regime_id": 8, "regime_name": "steep_slope_normal_level",
     "family": "B_normal_vol", "action_default": "calendar_spread_long_short",
     "asymmetry_note": "convex_decay_with_event", "intensity_count": 0},
    {"pattern": "(0,0,++)", "regime_id": 8, "regime_name": "steep_slope_normal_level",
     "family": "B_normal_vol", "action_default": "calendar_spread_long_short",
     "asymmetry_note": "convex_decay_with_event", "intensity_count": 1},
    {"pattern": "(0,0,--)", "regime_id": 9, "regime_name": "stress_local_naissant",
     "family": "B_normal_vol", "action_default": "size_reduce_monitor",
     "asymmetry_note": "transition_to_stressed", "intensity_count": 1},
    {"pattern": "(0,0,-)", "regime_id": 9, "regime_name": "stress_local_naissant",
     "family": "B_normal_vol", "action_default": "size_reduce_monitor",
     "asymmetry_note": "transition_to_stressed", "intensity_count": 0},
    {"pattern": "(0,+,+)", "regime_id": 10, "regime_name": "cross_product_divergence",
     "family": "B_normal_vol", "action_default": "short_short_long_long_hedged_gamma",
     "asymmetry_note": "terminal_cycle_repricing", "intensity_count": 0},
    {"pattern": "(0,+,++)", "regime_id": 10, "regime_name": "cross_product_divergence",
     "family": "B_normal_vol", "action_default": "short_short_long_long_hedged_gamma",
     "asymmetry_note": "terminal_cycle_repricing", "intensity_count": 1},
    {"pattern": "(0,++,+)", "regime_id": 10, "regime_name": "cross_product_divergence",
     "family": "B_normal_vol", "action_default": "short_short_long_long_hedged_gamma",
     "asymmetry_note": "terminal_cycle_repricing", "intensity_count": 1},
    {"pattern": "(0,++,++)", "regime_id": 10, "regime_name": "cross_product_divergence",
     "family": "B_normal_vol", "action_default": "short_short_long_long_hedged_gamma",
     "asymmetry_note": "terminal_cycle_repricing", "intensity_count": 2},
    {"pattern": "(+,0,0)", "regime_id": 11, "regime_name": "elevated_calm",
     "family": "C_high_vol", "action_default": "structural_hot_regime",
     "asymmetry_note": "new_setpoint_no_divergence", "intensity_count": 0},
    {"pattern": "(++,0,0)", "regime_id": 11, "regime_name": "elevated_calm",
     "family": "C_high_vol", "action_default": "structural_hot_regime",
     "asymmetry_note": "new_setpoint_no_divergence", "intensity_count": 1},
    {"pattern": "(+,+,0)", "regime_id": 12, "regime_name": "active_stress_normal_slope",
     "family": "C_high_vol", "action_default": "reduced_sizing",
     "asymmetry_note": "resolution_phase_unstable", "intensity_count": 0},
    {"pattern": "(+,++,0)", "regime_id": 12, "regime_name": "active_stress_normal_slope",
     "family": "C_high_vol", "action_default": "reduced_sizing",
     "asymmetry_note": "resolution_phase_unstable", "intensity_count": 1},
    {"pattern": "(++,+,0)", "regime_id": 12, "regime_name": "active_stress_normal_slope",
     "family": "C_high_vol", "action_default": "reduced_sizing",
     "asymmetry_note": "resolution_phase_unstable", "intensity_count": 1},
    {"pattern": "(++,++,0)", "regime_id": 12, "regime_name": "active_stress_normal_slope",
     "family": "C_high_vol", "action_default": "reduced_sizing",
     "asymmetry_note": "resolution_phase_unstable", "intensity_count": 2},
    {"pattern": "(+,+,--)", "regime_id": 13, "regime_name": "full_stress",
     "family": "C_high_vol", "action_default": "no_directional_vol_focus_microstructure",
     "asymmetry_note": "active_crisis", "intensity_count": 1},
    {"pattern": "(+,+,-)", "regime_id": 13, "regime_name": "full_stress",
     "family": "C_high_vol", "action_default": "no_directional_vol_focus_microstructure",
     "asymmetry_note": "active_crisis", "intensity_count": 0},
    {"pattern": "(+,++,--)", "regime_id": 13, "regime_name": "full_stress",
     "family": "C_high_vol", "action_default": "no_directional_vol_focus_microstructure",
     "asymmetry_note": "active_crisis", "intensity_count": 2},
    {"pattern": "(+,++,-)", "regime_id": 13, "regime_name": "full_stress",
     "family": "C_high_vol", "action_default": "no_directional_vol_focus_microstructure",
     "asymmetry_note": "active_crisis", "intensity_count": 1},
    {"pattern": "(++,+,--)", "regime_id": 13, "regime_name": "full_stress",
     "family": "C_high_vol", "action_default": "no_directional_vol_focus_microstructure",
     "asymmetry_note": "active_crisis", "intensity_count": 2},
    {"pattern": "(++,+,-)", "regime_id": 13, "regime_name": "full_stress",
     "family": "C_high_vol", "action_default": "no_directional_vol_focus_microstructure",
     "asymmetry_note": "active_crisis", "intensity_count": 1},
    {"pattern": "(++,++,--)", "regime_id": 13, "regime_name": "full_stress",
     "family": "C_high_vol", "action_default": "no_directional_vol_focus_microstructure",
     "asymmetry_note": "active_crisis", "intensity_count": 3},
    {"pattern": "(++,++,-)", "regime_id": 13, "regime_name": "full_stress",
     "family": "C_high_vol", "action_default": "no_directional_vol_focus_microstructure",
     "asymmetry_note": "active_crisis", "intensity_count": 2},
    {"pattern": "(+,0,--)", "regime_id": 14, "regime_name": "elevated_calm_inverted_curve",
     "family": "C_high_vol", "action_default": "convergence_trade_patient",
     "asymmetry_note": "exit_of_stress", "intensity_count": 1},
    {"pattern": "(+,0,-)", "regime_id": 14, "regime_name": "elevated_calm_inverted_curve",
     "family": "C_high_vol", "action_default": "convergence_trade_patient",
     "asymmetry_note": "exit_of_stress", "intensity_count": 0},
    {"pattern": "(++,0,--)", "regime_id": 14, "regime_name": "elevated_calm_inverted_curve",
     "family": "C_high_vol", "action_default": "convergence_trade_patient",
     "asymmetry_note": "exit_of_stress", "intensity_count": 2},
    {"pattern": "(++,0,-)", "regime_id": 14, "regime_name": "elevated_calm_inverted_curve",
     "family": "C_high_vol", "action_default": "convergence_trade_patient",
     "asymmetry_note": "exit_of_stress", "intensity_count": 1},
    {"pattern": "(+,+,+)", "regime_id": 15, "regime_name": "stressed_contango_persists",
     "family": "C_high_vol", "action_default": "observation_only",
     "asymmetry_note": "pre_crisis_signature", "intensity_count": 0},
    {"pattern": "(+,+,++)", "regime_id": 15, "regime_name": "stressed_contango_persists",
     "family": "C_high_vol", "action_default": "observation_only",
     "asymmetry_note": "pre_crisis_signature", "intensity_count": 1},
    {"pattern": "(+,++,+)", "regime_id": 15, "regime_name": "stressed_contango_persists",
     "family": "C_high_vol", "action_default": "observation_only",
     "asymmetry_note": "pre_crisis_signature", "intensity_count": 1},
    {"pattern": "(+,++,++)", "regime_id": 15, "regime_name": "stressed_contango_persists",
     "family": "C_high_vol", "action_default": "observation_only",
     "asymmetry_note": "pre_crisis_signature", "intensity_count": 2},
    {"pattern": "(++,+,+)", "regime_id": 15, "regime_name": "stressed_contango_persists",
     "family": "C_high_vol", "action_default": "observation_only",
     "asymmetry_note": "pre_crisis_signature", "intensity_count": 1},
    {"pattern": "(++,+,++)", "regime_id": 15, "regime_name": "stressed_contango_persists",
     "family": "C_high_vol", "action_default": "observation_only",
     "asymmetry_note": "pre_crisis_signature", "intensity_count": 2},
    {"pattern": "(++,++,+)", "regime_id": 15, "regime_name": "stressed_contango_persists",
     "family": "C_high_vol", "action_default": "observation_only",
     "asymmetry_note": "pre_crisis_signature", "intensity_count": 2},
    {"pattern": "(++,++,++)", "regime_id": 15, "regime_name": "stressed_contango_persists",
     "family": "C_high_vol", "action_default": "observation_only",
     "asymmetry_note": "pre_crisis_signature", "intensity_count": 3},
    {"pattern": "unmapped_extreme", "regime_id": 99, "regime_name": "unmapped_extreme",
     "family": "Z_fallback", "action_default": "observation_only_log_for_review",
     "asymmetry_note": "tail_combination_unseen_in_15_base_regimes", "intensity_count": 0},
]
