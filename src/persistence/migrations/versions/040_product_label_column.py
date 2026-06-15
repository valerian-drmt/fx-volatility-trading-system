"""Add the user-friendly ``product_label`` twin column on 4 tables.

R10.2 (2.1). Nullable VARCHAR(40) on open_position, open_position_history,
trade_structure, trade_preview. Backfilled with ``core.products.
product_label_from_symbol`` — the same helper the engine writers call going
forward, so the column never drifts from production logic.

Revision ID: 040_product_label_column
Revises: 039_fold_signal_tracking
Create Date: 2026-06-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from core.products import product_label_from_symbol

revision: str = "040_product_label_column"
down_revision: str | None = "039_fold_signal_tracking"
branch_labels: str | None = None
depends_on: str | None = None


# (table, ib_symbol_column_or_None, structure_type_column_or_None)
_BACKFILL_TARGETS: tuple[tuple[str, str | None, str | None], ...] = (
    ("open_position",         "structure", None),
    ("open_position_history", "structure", None),
    ("trade_structure",       None,        "structure_type"),
    ("trade_preview",         None,        "structure_type"),
)


def upgrade() -> None:
    for table, _, _ in _BACKFILL_TARGETS:
        op.add_column(table, sa.Column("product_label", sa.String(40), nullable=True))

    bind = op.get_bind()
    for table, sym_col, st_col in _BACKFILL_TARGETS:
        cols = ["id"] + [c for c in (sym_col, st_col) if c]
        rows = bind.execute(sa.text(f"SELECT {', '.join(cols)} FROM {table}")).mappings().all()
        updates = []
        for row in rows:
            label = product_label_from_symbol(
                row.get(sym_col) if sym_col else None,
                row.get(st_col) if st_col else None,
            )
            if label is not None:
                updates.append({"id": row["id"], "label": label})
        if updates:
            bind.execute(
                sa.text(f"UPDATE {table} SET product_label = :label WHERE id = :id"),
                updates,
            )


def downgrade() -> None:
    for table, _, _ in reversed(_BACKFILL_TARGETS):
        op.drop_column(table, "product_label")
