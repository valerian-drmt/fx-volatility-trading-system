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

Downgrade re-creates the three tables and re-seeds them from the
canonical Python dicts so rollback is loss-less.

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
    from core.regime_patterns import REGIME_PATTERNS
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
        list(REGIME_PATTERNS.values()),
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
    from core.pca_recommendations import PCA_RECOMMENDATIONS
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
        [
            {
                "pc_id": pc, "signal_label": lab,
                "recommended_structure": rec["recommended_structure"],
                "default_tenor": rec["default_tenor"],
                "description": rec["description"],
                "rationale": rec["rationale"],
            }
            for (pc, lab), rec in PCA_RECOMMENDATIONS.items()
        ],
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
    from core.trade_preview import TEMPLATES
    catalog_rows = [
        {
            "structure_type": k,
            "display_name": v["display"],
            "leg_template": v["legs"],
            "min_legs": len(v["legs"]),
            "max_legs": len(v["legs"]),
            "requires_delta_hedge": v["requires_delta_hedge"],
            "typical_vega_sign": v["vega_sign"],
            "typical_gamma_sign": v.get("typical_gamma_sign", "neutral"),
            "typical_theta_sign": v.get("typical_theta_sign", "neutral"),
            "description": v.get("description"),
            "rationale_for_pc": v.get("rationale_for_pc"),
        }
        for k, v in TEMPLATES.items()
        if v.get("in_catalog")
    ]
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
        catalog_rows,
    )
