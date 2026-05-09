"""Recreate ``positions`` with EXACTLY the 16 panel-E columns, in order.

Final schema, in physical order :

    id · local_symbol · side · tenor · maturity · quantity · nominal_eur ·
    contract_price_entry · market_price · current_pnl_usd · delta_usd ·
    gamma_usd · vega_usd · theta_usd · entry_timestamp · updated_at

No more ``status`` / ``closed_at`` / ``created_at`` : closed positions are
DELETEd from the table by execution-engine when IB no longer reports them.
The audit trail of closed positions lives in ``trades`` + ``position_snapshots``.

Tenor is stored (refreshed every sync cycle by execution-engine) to keep
the table self-sufficient for direct DB browsing.

Revision ID: 028_positions_panel_exact
Revises: 027_positions_closed_at
Create Date: 2026-05-09
"""
from __future__ import annotations

from alembic import op

revision: str = "028_positions_panel_exact"
down_revision: str | None = "027_positions_closed_at"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. Drop FKs that reference positions.id — we'll recreate them after rename.
    op.drop_constraint("position_snapshots_position_id_fkey",
                       "position_snapshots", type_="foreignkey")
    op.drop_constraint("trades_position_id_fkey", "trades", type_="foreignkey")

    # 2. Drop indexes / constraints that depend on columns being removed.
    op.drop_index("ix_positions_local_symbol_open", table_name="positions")
    op.drop_constraint("ck_positions_side", "positions", type_="check")

    # 3. Create the new table with columns in the exact required order.
    op.execute("""
        CREATE TABLE positions_new (
            id                   SERIAL PRIMARY KEY,
            local_symbol         VARCHAR(20) NOT NULL,
            side                 VARCHAR(4)  NOT NULL,
            tenor                VARCHAR(10),
            maturity             DATE,
            quantity             NUMERIC(15,4) NOT NULL,
            nominal_eur          NUMERIC(15,2),
            contract_price_entry NUMERIC(15,8),
            market_price         NUMERIC(15,8),
            current_pnl_usd      NUMERIC(15,2),
            delta_usd            NUMERIC(15,2),
            gamma_usd            NUMERIC(15,2),
            vega_usd             NUMERIC(15,2),
            theta_usd            NUMERIC(15,2),
            entry_timestamp      TIMESTAMPTZ NOT NULL,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_positions_side CHECK (side IN ('BUY','SELL'))
        )
    """)

    # 4. Copy OPEN positions only (closed_at IS NULL after migration 027).
    op.execute("""
        INSERT INTO positions_new (
            id, local_symbol, side, tenor, maturity, quantity, nominal_eur,
            contract_price_entry, market_price, current_pnl_usd, delta_usd,
            gamma_usd, vega_usd, theta_usd, entry_timestamp, updated_at
        )
        SELECT
            id, local_symbol, side,
            CASE
              WHEN maturity IS NULL THEN NULL
              WHEN (maturity - CURRENT_DATE) <  0  THEN 'expired'
              WHEN (maturity - CURRENT_DATE) <= 10 THEN '1W'
              WHEN (maturity - CURRENT_DATE) <= 21 THEN '2W'
              WHEN (maturity - CURRENT_DATE) <= 45 THEN '1M'
              WHEN (maturity - CURRENT_DATE) <= 75 THEN '2M'
              WHEN (maturity - CURRENT_DATE) <= 105 THEN '3M'
              WHEN (maturity - CURRENT_DATE) <= 165 THEN '6M'
              WHEN (maturity - CURRENT_DATE) <= 270 THEN '9M'
              WHEN (maturity - CURRENT_DATE) <= 460 THEN '1Y'
              ELSE '2Y+'
            END,
            maturity, quantity, nominal_eur, contract_price_entry,
            market_price, current_pnl_usd, delta_usd, gamma_usd, vega_usd,
            theta_usd, entry_timestamp, updated_at
        FROM positions
        WHERE closed_at IS NULL
    """)

    # 5. Sync the sequence with the max id we just copied.
    op.execute("""
        SELECT setval('positions_new_id_seq',
                      COALESCE((SELECT MAX(id) FROM positions_new), 1),
                      true)
    """)

    # 6. Swap : drop old table, rename new.
    op.execute("DROP TABLE positions")
    op.rename_table("positions_new", "positions")
    op.execute("ALTER SEQUENCE positions_new_id_seq RENAME TO positions_id_seq")

    # 7. Recreate FKs + uniqueness index (each open contract appears once).
    op.create_foreign_key(
        "position_snapshots_position_id_fkey",
        "position_snapshots", "positions",
        ["position_id"], ["id"],
    )
    op.create_foreign_key(
        "trades_position_id_fkey",
        "trades", "positions",
        ["position_id"], ["id"],
    )
    op.create_index(
        "ix_positions_local_symbol_unique",
        "positions",
        ["local_symbol"],
        unique=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 028 is one-way : recreating the legacy schema (status, "
        "closed_at, created_at, etc.) is not supported. If you need to roll "
        "back, restore from a snapshot taken before applying this migration."
    )
