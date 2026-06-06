"""Rename 4 tables for naming consistency :

  - ``vol_engine_config``         → ``config_vol_engine``
  - ``exit_rules_config``         → ``config_exit_rules``
  - ``regime_snapshot``           → ``regime_snapshot_history``
  - ``book_state_snapshot``       → ``book_state_snapshot_history``

Why :

  (A) Two of the four ``Config`` domain tables used a ``_config`` suffix
      while ``config_scalar`` + ``config_ib_session`` already use a
      ``config_`` prefix. Aligning all four on the prefix lets a DBA
      query the whole domain via ``\\dt config_*``.

  (B) Two append-only per-cycle audit tables (``regime_snapshot``,
      ``book_state_snapshot``) were the odd ones out — every other
      timeseries-style table in the schema ends in ``_history``
      (``feature_history``, ``vol_surface_history``,
      ``pca_signal_history``, ``open_position_history``,
      ``booked_position_metric_history``, ``account_history``,
      ``pca_surface_snapshot_history``). Consistent suffix → a DBA reads
      the prefix and instantly knows the row cardinality pattern
      (snapshot = singleton state ; history = N rows/cycle).

The ``ck_regime_snapshots_label`` CHECK constraint is renamed to match
the new table name so the constraint name stays self-describing (the
old name even carried the historical ``snapshots`` plural typo).

Revision ID: 040_rename_config_history_tables
Revises: 039_drop_lookup_tables
Create Date: 2026-06-06
"""
from __future__ import annotations

from alembic import op

revision: str = "040_rename_config_history_tables"
down_revision: str | None = "039_drop_lookup_tables"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── Domain Config : prefix alignment ──
    op.rename_table("vol_engine_config", "config_vol_engine")
    op.rename_table("exit_rules_config", "config_exit_rules")

    # ── Audit timeseries : _history suffix alignment ──
    op.rename_table("regime_snapshot", "regime_snapshot_history")
    op.execute(
        "ALTER TABLE regime_snapshot_history RENAME CONSTRAINT "
        "ck_regime_snapshots_label TO ck_regime_snapshot_history_label"
    )

    op.rename_table("book_state_snapshot", "book_state_snapshot_history")


def downgrade() -> None:
    op.rename_table("book_state_snapshot_history", "book_state_snapshot")

    op.execute(
        "ALTER TABLE regime_snapshot_history RENAME CONSTRAINT "
        "ck_regime_snapshot_history_label TO ck_regime_snapshots_label"
    )
    op.rename_table("regime_snapshot_history", "regime_snapshot")

    op.rename_table("config_exit_rules", "exit_rules_config")
    op.rename_table("config_vol_engine", "vol_engine_config")
