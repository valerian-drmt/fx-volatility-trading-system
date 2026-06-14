"""Rename 5 vol / indicator tables to the ``<role>_history`` target naming.

Pure ``ALTER TABLE … RENAME`` — data, foreign keys, indexes and unique
constraints all follow the table automatically. Constraint *names* are
intentionally left untouched (they keep encoding the old table name) so the
migration stays a clean, fully-reversible rename with no DROP/recreate — the
round-trip is therefore unbounded for this revision.

R10.1 Theme 1 (re-derived fresh on the live ``031`` head — NOT a replay of the
divergent r10 chain). The two-step r10 path (intermediate ``regime_snapshot`` /
``surface_pca_snapshot_history`` then a tail ``*_history`` rename) is collapsed
into a single direct-to-final rename here, so the tail migrations no longer
touch these two tables.

Renames :

  vol_surface_snapshot       -> vol_surface_history
  regime_feature_snapshot    -> regime_snapshot_history
  macro_event                -> event_calendar
  surface_snapshots_hourly   -> pca_surface_snapshot_history
  pca_projection_snapshot    -> pca_signal_history

Revision ID: 032_rename_vol_indicator_tables
Revises: 031_iv_vanna_volga
Create Date: 2026-06-14
"""
from __future__ import annotations

from alembic import op

revision: str = "032_rename_vol_indicator_tables"
down_revision: str | None = "031_iv_vanna_volga"
branch_labels: str | None = None
depends_on: str | None = None


# (old_name, new_name) — applied in order on upgrade, reversed on downgrade.
_RENAMES: tuple[tuple[str, str], ...] = (
    ("vol_surface_snapshot",     "vol_surface_history"),
    ("regime_feature_snapshot",  "regime_snapshot_history"),
    ("macro_event",              "event_calendar"),
    ("surface_snapshots_hourly", "pca_surface_snapshot_history"),
    ("pca_projection_snapshot",  "pca_signal_history"),
)


def upgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(old, new)


def downgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(new, old)
