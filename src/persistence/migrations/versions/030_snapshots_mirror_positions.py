"""Recreate ``position_snapshots`` to mirror the ``positions`` schema.

Each cycle of risk-engine writes a copy of the live positions table into
this history table. Same columns, plus ``id`` PK, ``position_id`` FK and
``timestamp``.

FK ``position_snapshots.position_id → positions.id`` uses ``ON DELETE CASCADE``
so closed positions (DELETEd by execution-engine) automatically drop their
snapshots — consistent with the "positions table = OPEN only" invariant.

Revision ID: 030_snapshots_mirror_positions
Revises: 029_positions_rename_cols
Create Date: 2026-05-09
"""
from __future__ import annotations

from alembic import op

revision: str = "030_snapshots_mirror_positions"
down_revision: str | None = "029_positions_rename_cols"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("DROP TABLE position_snapshots")
    op.execute("""
        CREATE TABLE position_snapshots (
            id                   SERIAL PRIMARY KEY,
            position_id          INTEGER NOT NULL
                                  REFERENCES positions(id) ON DELETE CASCADE,
            timestamp            TIMESTAMPTZ NOT NULL,
            structure            VARCHAR(20) NOT NULL,
            side                 VARCHAR(4) NOT NULL,
            tenor                VARCHAR(10),
            expiry               DATE,
            quantity             NUMERIC(15,4) NOT NULL,
            nominal_eur          NUMERIC(15,2),
            contract_price_entry NUMERIC(15,8),
            market_price         NUMERIC(15,8),
            current_pnl_usd      NUMERIC(15,2),
            delta_usd            NUMERIC(15,2),
            gamma_usd            NUMERIC(15,2),
            vega_usd             NUMERIC(15,2),
            theta_usd            NUMERIC(15,2)
        )
    """)
    op.create_index(
        "ix_position_snapshots_position_ts",
        "position_snapshots",
        ["position_id", "timestamp"],
    )
    op.create_index(
        "ix_position_snapshots_ts",
        "position_snapshots",
        ["timestamp"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 030 is one-way : the legacy snapshots schema (spot, iv, "
        "pnl_usd) is not recoverable from the new shape. Restore from a "
        "snapshot taken before applying this migration if needed."
    )
