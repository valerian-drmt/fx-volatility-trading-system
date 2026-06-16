"""Drop the regime + PCA-recommendation static lookup mirror tables.

R10.2 — both tables were static seeds mirroring data that now lives in code
constants (``core.regime_patterns.REGIME_PATTERNS`` and
``core.pca_recommendations.PCA_RECOMMENDATIONS``). Two sources of truth for the
same numbers ⇒ drift risk for zero value. Consumers (regime_features, signals
router, vol-engine) now read the constants. Downgrade recreates the tables (no
re-seed — the seed scripts live in scripts/dev and can repopulate if needed).

  DROP regime_pattern_dict           (ORM RegimeLookup)
  DROP pca_structure_recommendation  (ORM SignalRecommendationsMap)

Revision ID: 042_drop_lookup_mirrors
Revises: 041_product_identity_schema
Create Date: 2026-06-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "042_drop_lookup_mirrors"
down_revision: str | None = "041_product_identity_schema"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("pca_structure_recommendation")
    op.drop_table("regime_pattern_dict")


def downgrade() -> None:
    op.create_table(
        "regime_pattern_dict",
        sa.Column("pattern", sa.String(20), primary_key=True),
        sa.Column("regime_id", sa.Integer, nullable=False),
        sa.Column("regime_name", sa.String(60), nullable=False),
        sa.Column("family", sa.String(40), nullable=False),
        sa.Column("action_default", sa.String(80), nullable=False),
        sa.Column("asymmetry_note", sa.String(120)),
        sa.Column("intensity_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "pca_structure_recommendation",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("pc_id", sa.Integer, nullable=False),
        sa.Column("signal_label", sa.String(15), nullable=False),
        sa.Column("recommended_structure", sa.String(60), nullable=False),
        sa.Column("default_tenor", sa.String(10), nullable=False),
        sa.Column("description", sa.String(200)),
        sa.Column("rationale", sa.String(500)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("TRUE")),
        sa.UniqueConstraint("pc_id", "signal_label", "is_active", name="uq_signal_rec_map_pc_label_active"),
        sa.CheckConstraint("signal_label IN ('CHEAP','EXPENSIVE')", name="ck_signal_rec_map_label"),
    )
