"""Rename ``surface_pca_snapshot_history`` → ``pca_surface_snapshot_history``.

Why : the four tables backing the PCA pipeline now all start with the
``pca_`` prefix, except this one which still carried the legacy
``surface_`` prefix from the pre-PCA "vol surface snapshot" intent.

    pca_model                                         ✓
    pca_signal_history                                ✓
    pca_structure_recommendation                      ✓
    pca_surface_snapshot_history (← was surface_pca_)

Aligning the prefix gives a one-glance view of "what belongs to PCA"
in the DB Schema dev tab and in any ``\\dt pca_*`` psql query.

No index renames are required : indexes are tied to the table by OID,
not by name, and Postgres' rename machinery keeps them attached.

Revision ID: 036_rename_pca_surface_snapshot
Revises: 035_trade_order_ib_local_symbol
Create Date: 2026-06-06
"""
from __future__ import annotations

from alembic import op

revision: str = "036_rename_pca_surface_snapshot"
down_revision: str | None = "035_trade_order_ib_local_symbol"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.rename_table(
        "surface_pca_snapshot_history",
        "pca_surface_snapshot_history",
    )


def downgrade() -> None:
    op.rename_table(
        "pca_surface_snapshot_history",
        "surface_pca_snapshot_history",
    )
