"""Rename ``local_symbol`` → ``structure`` and ``maturity`` → ``expiry``.

Aligns the DB column names with the panel E vocabulary.

Revision ID: 029_positions_rename_cols
Revises: 028_positions_panel_exact
Create Date: 2026-05-09
"""
from __future__ import annotations

from alembic import op

revision: str = "029_positions_rename_cols"
down_revision: str | None = "028_positions_panel_exact"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.alter_column("positions", "local_symbol", new_column_name="structure")
    op.alter_column("positions", "maturity", new_column_name="expiry")
    # Rename the unique index too — keeps grep-ability with the new column name.
    op.execute(
        "ALTER INDEX ix_positions_local_symbol_unique "
        "RENAME TO ix_positions_structure_unique"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX ix_positions_structure_unique "
        "RENAME TO ix_positions_local_symbol_unique"
    )
    op.alter_column("positions", "expiry", new_column_name="maturity")
    op.alter_column("positions", "structure", new_column_name="local_symbol")
