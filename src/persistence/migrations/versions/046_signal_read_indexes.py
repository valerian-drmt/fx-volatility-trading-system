"""Indexes for the Signal-tab read paths.

Two covering indexes, both serving latest-by-symbol lookups:

  * ``ix_pca_signal_symbol_pc_ts`` on ``pca_signal_history
    (symbol, pc_id, timestamp DESC)`` -- serves ``/signals/pca/state``
    (DISTINCT ON (pc_id) latest per PC) and ``/signals/pca/history``
    (filter by symbol+pc_id, ORDER BY timestamp DESC LIMIT n).
  * ``ix_vol_surface_underlying_ts`` on ``vol_surface_history
    (underlying, timestamp DESC)`` -- the equivalent covering index on the old
    ``vol_surfaces`` table was lost in the rename chain. Serves
    latest-by-symbol + surface_at lookups.

Index-only, additive, no column change -> no writer/reader/payload cascade.

Revision ID: 046_signal_read_indexes
Revises: 045_index_open_position_history
Create Date: 2026-07-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "046_signal_read_indexes"
down_revision: str | None = "045_index_open_position_history"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "ix_pca_signal_symbol_pc_ts",
        "pca_signal_history",
        ["symbol", "pc_id", sa.text("timestamp DESC")],
    )
    op.create_index(
        "ix_vol_surface_underlying_ts",
        "vol_surface_history",
        ["underlying", sa.text("timestamp DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_vol_surface_underlying_ts",
        table_name="vol_surface_history",
        if_exists=True,
    )
    op.drop_index(
        "ix_pca_signal_symbol_pc_ts",
        table_name="pca_signal_history",
        if_exists=True,
    )
