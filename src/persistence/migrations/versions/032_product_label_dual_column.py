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

Backfill : pure-Python data migration using a frozen copy of
``core.products.product_label_from_symbol`` (inlined below — migrations
must never import live ``core`` modules, which can be refactored or
deleted after the fact and would break replaying this revision).

Nullable for now ; a follow-up migration (033) will promote to NOT NULL
once writer coverage is proven across one release cycle.

Revision ID: 032_product_label_dual_column
Revises: 026_theme2_portfolio
Create Date: 2026-06-03
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

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


# ── Frozen copy of core.products.product_label_from_symbol (as of this
#    revision) — inlined so the migration never depends on a live module. ──

_STRUCTURE_TYPE_TO_LABEL: dict[str, str] = {
    "vanilla_call":         "Vanilla Call",
    "short_vanilla_call":   "Vanilla Call",
    "vanilla_put":          "Vanilla Put",
    "short_vanilla_put":    "Vanilla Put",
    "straddle_atm":         "Straddle",
    "short_straddle_atm":   "Straddle",
    "long_strangle_25d":    "Strangle",
    "short_strangle":       "Strangle",
    "long_butterfly_25d":   "Butterfly",
    "short_butterfly_25d":  "Butterfly",
    "calendar_long":        "Calendar",
    "calendar_short":       "Calendar",
}


def _future_label(ib_symbol: str | None) -> str:
    return "Future - M6E" if ib_symbol and ib_symbol.startswith("M6E") else "Future - 6E"


def product_label_from_symbol(
    ib_symbol: str | None,
    structure_type: str | None,
) -> str | None:
    """Return the user-friendly product label (frozen copy, see above).

    Resolution order :
        1. ``structure_type`` (highest signal — exec pipeline writes it).
        2. ``ib_symbol`` parse (IB-live positions that bypass trade_structure).

    Returns ``None`` when neither input is recognised. Never raises.
    """
    if structure_type:
        if structure_type.startswith("future_"):
            return _future_label(ib_symbol)
        label = _STRUCTURE_TYPE_TO_LABEL.get(structure_type)
        if label is not None:
            return label
    if not ib_symbol:
        return None
    sym = ib_symbol.strip()
    if not sym:
        return None
    # IB option localSymbols look like "EUUQ6 C1130" / "EUUN6 P1170" :
    # 5 chars + space + C/P + strike. The space-delimited C/P token is the
    # most reliable signal across all CME FX-option series.
    if " C" in sym:
        return "Vanilla Call"
    if " P" in sym:
        return "Vanilla Put"
    # Otherwise treat as a future symbol (6E* full / M6E* micro).
    return _future_label(sym)


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
