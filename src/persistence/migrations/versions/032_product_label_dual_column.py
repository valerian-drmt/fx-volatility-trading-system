"""Add ``product_label`` user-friendly twin column alongside existing
structure identifiers.

Sites :
  - ``position.product_label``                    (IB-derived)
  - ``position_metric_history.product_label``     (mirror)
  - ``trade_structure.product_label``             (structure_type-derived)
  - ``trade_preview.product_label``               (structure_type-derived)

The label is one of 8 canonical values from
``core.products.PRODUCT_LABELS`` :

    Vanilla Call · Vanilla Put · Straddle · Strangle · Butterfly ·
    Calendar · Future - 6E · Future - M6E

Backfill : pure-Python data migration using ``core.products.
product_label_from_symbol`` — same helper that the engine writers will
call going forward, so the column never drifts from production logic.

Nullable for now ; a follow-up migration (033) will promote to NOT NULL
once writer coverage is proven across one release cycle.

Revision ID: 032_product_label_dual_column
Revises: 026_theme2_portfolio
Create Date: 2026-06-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from core.products import product_label_from_symbol

revision: str = "032_product_label_dual_column"
down_revision: str | None = "026_theme2_portfolio"
branch_labels: str | None = None
depends_on: str | None = None


# (table_name, ib_symbol_column_or_None, structure_type_column_or_None)
# When ib_symbol_column is None, the helper only sees structure_type and
# the symbol-parse fallback never fires (correct for trade_preview /
# trade_structure rows that pre-date execution).
_BACKFILL_TARGETS: tuple[tuple[str, str | None, str | None], ...] = (
    ("position",                "structure",      None),
    ("position_metric_history", "structure",      None),
    ("trade_structure",         None,             "structure_type"),
    ("trade_preview",           None,             "structure_type"),
)


def upgrade() -> None:
    # 1. Schema : ADD COLUMN product_label VARCHAR(40) NULL on all 4 tables.
    for table, _, _ in _BACKFILL_TARGETS:
        op.add_column(
            table,
            sa.Column("product_label", sa.String(40), nullable=True),
        )

    # 2. Backfill via the canonical helper. We use a raw connection +
    #    executemany for the UPDATEs ; no ORM session, no autocommit
    #    surprises. Rows with NULL/garbage inputs stay NULL (helper
    #    returns None) — operationally fine because the column is
    #    nullable in this migration.
    bind = op.get_bind()
    for table, sym_col, st_col in _BACKFILL_TARGETS:
        select_cols = ["id"]
        if sym_col:
            select_cols.append(sym_col)
        if st_col:
            select_cols.append(st_col)
        rows = bind.execute(sa.text(
            f"SELECT {', '.join(select_cols)} FROM {table}"
        )).mappings().all()

        updates: list[dict[str, object]] = []
        for row in rows:
            label = product_label_from_symbol(
                row.get(sym_col) if sym_col else None,
                row.get(st_col)  if st_col  else None,
            )
            if label is not None:
                updates.append({"id": row["id"], "label": label})

        if updates:
            bind.execute(
                sa.text(
                    f"UPDATE {table} SET product_label = :label "
                    f"WHERE id = :id"
                ),
                updates,
            )


def downgrade() -> None:
    for table, _, _ in reversed(_BACKFILL_TARGETS):
        op.drop_column(table, "product_label")
