"""Rename 11 vol-domain tables to consistent ``<domain>_<role>`` naming.

Each table is renamed in place ; data is preserved (alembic uses
``ALTER TABLE … RENAME``). Foreign keys, indexes and unique
constraints follow the table automatically — only constraint *names*
get bumped where they encoded the old table name.

Renames :

  vol_surfaces                -> vol_surface_snapshot
  signals                     -> vol_pricing_signal_snapshot
  regime_snapshots            -> regime_feature_snapshot
  pca_signals                 -> pca_projection_snapshot
  pca_models                  -> pca_model
  events                      -> macro_event
  regime_lookup_table         -> regime_pattern_dict
  signal_recommendations_map  -> pca_structure_recommendation
  vrp_table_default           -> vrp_default_curve
  feature_history             -> feature_history_30d
  vol_config                  -> vol_engine_config

Revision ID: 020_rename_vol_tables
Revises: 019_drop_unused_vol_tables
Create Date: 2026-05-07
"""
from __future__ import annotations

from alembic import op

revision: str = "020_rename_vol_tables"
down_revision: str | None = "019_drop_unused_vol_tables"
branch_labels: str | None = None
depends_on: str | None = None


# (old_name, new_name) — applied in order on upgrade and reversed on downgrade.
_RENAMES: tuple[tuple[str, str], ...] = (
    ("vol_surfaces",               "vol_surface_snapshot"),
    ("signals",                    "vol_pricing_signal_snapshot"),
    ("regime_snapshots",           "regime_feature_snapshot"),
    ("pca_signals",                "pca_projection_snapshot"),
    ("pca_models",                 "pca_model"),
    ("events",                     "macro_event"),
    ("regime_lookup_table",        "regime_pattern_dict"),
    ("signal_recommendations_map", "pca_structure_recommendation"),
    ("vrp_table_default",          "vrp_default_curve"),
    ("feature_history",            "feature_history_30d"),
    ("vol_config",                 "vol_engine_config"),
)


def upgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(old, new)


def downgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(new, old)
