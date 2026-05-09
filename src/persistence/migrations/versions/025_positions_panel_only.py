"""Drop legacy operational columns from ``positions`` — panel E shape only.

After 024 added the panel-E denormalised columns, the legacy columns
``symbol`` / ``instrument_type`` / ``strike`` / ``option_type`` / ``entry_price``
/ ``multiplier`` are now redundant : the IB ``localSymbol`` (stored in
``local_symbol``) is the single canonical key. Engines reparse it via
``shared.contracts.parse_local_symbol`` when they need contract details.

Final schema after this migration matches Portfolio Panel section E :
    id · local_symbol · side · maturity · quantity · nominal_eur ·
    contract_price_entry · status · entry_timestamp · created_at · updated_at

Match keys used by ``execution-engine.position_sync`` switch from a 5-tuple
to a single ``WHERE local_symbol = c.localSymbol`` lookup. Fewer fields to
keep in sync, one canonical identifier shared with IB.

Revision ID: 025_positions_panel_only
Revises: 024_position_panel_cols
Create Date: 2026-05-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "025_positions_panel_only"
down_revision: str | None = "024_position_panel_cols"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Backfill ``local_symbol`` for any legacy row that pre-dates 024.
    #    Rebuild it from the operational columns one last time.
    op.execute("""
        UPDATE positions
           SET local_symbol = CASE
             WHEN instrument_type = 'FUTURE' AND symbol = 'EUR'
               THEN '6E' || SUBSTRING('FGHJKMNQUVXZ' FROM EXTRACT(MONTH FROM maturity)::int FOR 1)
                          || RIGHT(EXTRACT(YEAR FROM maturity)::text, 1)
             WHEN instrument_type = 'FUTURE' AND symbol = 'M6E'
               THEN 'M6E' || SUBSTRING('FGHJKMNQUVXZ' FROM EXTRACT(MONTH FROM maturity)::int FOR 1)
                           || RIGHT(EXTRACT(YEAR FROM maturity)::text, 1)
             WHEN instrument_type = 'OPTION' AND option_type IS NOT NULL AND strike IS NOT NULL
               THEN 'EUU' || SUBSTRING('FGHJKMNQUVXZ' FROM EXTRACT(MONTH FROM maturity)::int FOR 1)
                           || RIGHT(EXTRACT(YEAR FROM maturity)::text, 1)
                           || ' '
                           || CASE option_type WHEN 'CALL' THEN 'C' ELSE 'P' END
                           || LPAD((strike * 1000)::int::text, 4, '0')
             ELSE local_symbol
           END
         WHERE local_symbol IS NULL
    """)

    # 2. Drop legacy CHECK constraints on the columns we're about to remove.
    op.drop_constraint("ck_positions_instrument_type", "positions", type_="check")
    op.drop_constraint("ck_positions_option_type", "positions", type_="check")

    # 3. Drop the redundant columns. Side / quantity / status / entry_timestamp
    #    stay — they're either user-facing (panel E) or audit-shaped.
    op.drop_column("positions", "symbol")
    op.drop_column("positions", "instrument_type")
    op.drop_column("positions", "strike")
    op.drop_column("positions", "option_type")
    op.drop_column("positions", "entry_price")
    op.drop_column("positions", "multiplier")

    # 4. ``local_symbol`` becomes the canonical identifier — make it
    #    NOT NULL and indexed for the new sync match path.
    op.alter_column("positions", "local_symbol", nullable=False)
    op.create_index(
        "ix_positions_local_symbol_open",
        "positions",
        ["local_symbol"],
        postgresql_where=sa.text("status = 'OPEN'"),
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_positions_local_symbol_open", table_name="positions")
    op.alter_column("positions", "local_symbol", nullable=True)

    op.add_column("positions", sa.Column("multiplier", sa.Numeric(10, 2), nullable=True))
    op.add_column("positions", sa.Column("entry_price", sa.Numeric(15, 8), nullable=True))
    op.add_column("positions", sa.Column("option_type", sa.String(4), nullable=True))
    op.add_column("positions", sa.Column("strike", sa.Numeric(10, 5), nullable=True))
    op.add_column("positions", sa.Column("instrument_type", sa.String(10), nullable=True))
    op.add_column("positions", sa.Column("symbol", sa.String(20), nullable=True))

    op.create_check_constraint(
        "ck_positions_instrument_type",
        "positions",
        "instrument_type IN ('SPOT','FUTURE','OPTION')",
    )
    op.create_check_constraint(
        "ck_positions_option_type",
        "positions",
        "option_type IS NULL OR option_type IN ('CALL','PUT')",
    )
