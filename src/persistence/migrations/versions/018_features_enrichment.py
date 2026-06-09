"""Step 2 — features enrichment : 5 columns × 3 features on regime_snapshots,
plus regime_lookup_table (joint_pattern → regime mapping) and
vol_features_context_baseline (μ, σ, n_obs per (feature, event_type, days_bucket, tod_bucket)).

Cf. in-conversation E2 brief (15 base patterns expanded to 5-bucket variants).

Revision ID: 018_features_enrichment
Revises: 017_unwind_unique_includes_role
Create Date: 2026-05-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "018_features_enrichment"
down_revision: str | None = "017_unwind_unique_includes_role"
branch_labels: str | None = None
depends_on: str | None = None


_FEATURES = ("vol_level", "vol_of_vol", "term_slope")


def upgrade() -> None:
    # 1. New per-feature columns on regime_snapshots — nullable, populated
    #    going forward by the vol-engine cycle (E3). Historical rows stay NULL.
    for f in _FEATURES:
        op.add_column("regime_snapshots", sa.Column(f"bucket_{f}", sa.String(4)))
        op.add_column("regime_snapshots", sa.Column(f"delta_z_1h_{f}", sa.Float))
        op.add_column("regime_snapshots", sa.Column(f"pct_{f}", sa.Integer))
        op.add_column("regime_snapshots", sa.Column(f"signal_{f}", sa.String(8)))

    # CHECK constraints — kept loose ("--", "-", "0", "+", "++") so the column
    # can also store the literal '—' renderer? No — the renderer puts "—" in
    # the JSON only ; DB stays in the 5-bucket alphabet.
    for f in _FEATURES:
        op.create_check_constraint(
            f"ck_regime_snapshots_bucket_{f}",
            "regime_snapshots",
            f"bucket_{f} IS NULL OR bucket_{f} IN ('--','-','0','+','++')",
        )
        op.create_check_constraint(
            f"ck_regime_snapshots_signal_{f}",
            "regime_snapshots",
            f"signal_{f} IS NULL OR signal_{f} IN ('noise','weak','strong','tail')",
        )

    # 2. regime_lookup_table — pattern PK, regime mapping. Bootstrapped by
    #    scripts/seed_regime_lookup.py from the 15-pattern base × 5-bucket
    #    expansion (≤125 rows, ~30 active).
    op.create_table(
        "regime_lookup_table",
        sa.Column("pattern", sa.String(20), primary_key=True),
        sa.Column("regime_id", sa.Integer, nullable=False),
        sa.Column("regime_name", sa.String(60), nullable=False),
        sa.Column("family", sa.String(40), nullable=False),
        sa.Column("action_default", sa.String(80), nullable=False),
        sa.Column("asymmetry_note", sa.String(120)),
        sa.Column("intensity_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # 3. vol_features_context_baseline — μ, σ, n_obs lookup keyed by
    #    (feature, event_type, days_bucket, tod_bucket). Populated by the
    #    weekly batch job in E3.
    op.create_table(
        "vol_features_context_baseline",
        sa.Column("feature", sa.String(20), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),    # e.g. 'FOMC', 'none'
        sa.Column("days_bucket", sa.Integer, nullable=False),       # 0,1,2,3,4 → [0-1,2-3,4-5,6-10,>10]
        sa.Column("tod_bucket", sa.String(20), nullable=False),     # london_open / overlap / ny_close / asia
        sa.Column("mu", sa.Float, nullable=False),
        sa.Column("sigma", sa.Float, nullable=False),
        sa.Column("n_obs", sa.Integer, nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint(
            "feature", "event_type", "days_bucket", "tod_bucket",
            name="pk_vol_features_context_baseline",
        ),
        sa.CheckConstraint(
            "status IN ('valid','insufficient','stale')",
            name="ck_vol_features_context_baseline_status",
        ),
    )


def downgrade() -> None:
    op.drop_table("vol_features_context_baseline")
    op.drop_table("regime_lookup_table")
    for f in _FEATURES:
        op.drop_constraint(f"ck_regime_snapshots_signal_{f}", "regime_snapshots", type_="check")
        op.drop_constraint(f"ck_regime_snapshots_bucket_{f}", "regime_snapshots", type_="check")
    for f in _FEATURES:
        op.drop_column("regime_snapshots", f"signal_{f}")
        op.drop_column("regime_snapshots", f"pct_{f}")
        op.drop_column("regime_snapshots", f"delta_z_1h_{f}")
        op.drop_column("regime_snapshots", f"bucket_{f}")
