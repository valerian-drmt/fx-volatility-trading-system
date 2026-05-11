"""Theme 1 schema cleanup — rename 6 vol/indicator tables.

Audit-driven Variante B (cf. docs/db-schema-theme1-plan.md) :

  vol_surface_snapshot      → vol_surface_history
  surface_snapshots_hourly  → surface_pca_snapshot_history
  feature_history_30d       → feature_history
  regime_feature_snapshot   → regime_snapshot
  pca_projection_snapshot   → pca_signal_history
  macro_event               → event_calendar

  vrp_default_curve         → KEPT (initial audit thought it was dead ; deeper
                              re-check showed vol/engine._compute_regime reads
                              it at every cycle via select(VrpTableDefault) →
                              vrp_lookup dict. Values in DB seed differ from
                              core.vol.vrp.VRP_DEFAULTS_VOL_PTS so they can't
                              be unified safely. Drop deferred to Theme 4 :
                              fold the seed into the unified `config` table).

Indexes auto-rename in Postgres on rename_table. Constraint names that
embed the old table name (e.g. uq_vol_surface_snapshot_timestamp) keep
their old names — non-blocking but cosmetic, can be tidied in a future
chore migration.

Revision ID: 023_theme1_renames
Revises: 031_iv_vanna_volga
Create Date: 2026-05-11
"""
from __future__ import annotations

from alembic import op

revision: str = "023_theme1_renames"
down_revision: str | None = "031_iv_vanna_volga"
branch_labels: str | None = None
depends_on: str | None = None


_RENAMES: list[tuple[str, str]] = [
    ("vol_surface_snapshot",     "vol_surface_history"),
    ("surface_snapshots_hourly", "surface_pca_snapshot_history"),
    ("feature_history_30d",      "feature_history"),
    ("regime_feature_snapshot",  "regime_snapshot"),
    ("pca_projection_snapshot",  "pca_signal_history"),
    ("macro_event",              "event_calendar"),
]


def upgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(old, new)


def downgrade() -> None:
    for old, new in reversed(_RENAMES):
        op.rename_table(new, old)
