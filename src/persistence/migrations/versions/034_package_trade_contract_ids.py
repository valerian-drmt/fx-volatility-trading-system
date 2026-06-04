"""Introduce the 3-level Murex-aligned identity stack on ``open_position``.

Adds the structural identifiers for : **contract** (atomic instrument =
1 IB conId) → **trade** (strategy / structure regrouping legs) →
**package** (operational grouping of multiple trades).

  - ``open_position.contract_id``  : the IB ``conId`` (atomic instrument id).
  - ``open_position.trade_id``     : FK to ``trade_structure.id`` (the
                                     strategy / structure ; 2 legs of a
                                     straddle share one ``trade_id``).
  - ``open_position.package_id``   : FK to ``package.id`` (denormalised
                                     from ``trade_structure.package_id``
                                     for query convenience).

Also :
  - new empty ``package`` table (header + label + description) — populated
    later when the operator wants to group several trades into a single
    risk/funding envelope.
  - ``trade_structure.package_id`` FK to ``package.id`` (nullable).

All FKs use ``ON DELETE SET NULL`` so an OpenPosition row survives the
deletion of its trade / package upstream.

Both ``open_position`` and ``open_position_history`` are amended in
parallel so the history table stays 1-for-1 with the live table.

Revision ID: 034_package_trade_contract_ids
Revises: 033_open_position_rename
Create Date: 2026-06-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "034_package_trade_contract_ids"
down_revision: str | None = "033_open_position_rename"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. New ``package`` table — header + label.
    op.create_table(
        "package",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("description", sa.String(300), nullable=True),
    )

    # 2. ``trade_structure.package_id`` — FK to the new package table.
    op.add_column(
        "trade_structure",
        sa.Column("package_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_trade_structure_package_id",
        "trade_structure", "package",
        ["package_id"], ["id"],
        ondelete="SET NULL",
    )

    # 3. ``open_position`` + ``open_position_history`` : 3 new columns each.
    for table in ("open_position", "open_position_history"):
        op.add_column(
            table, sa.Column("contract_id", sa.BigInteger(), nullable=True),
        )
        op.add_column(
            table, sa.Column("trade_id", sa.BigInteger(), nullable=True),
        )
        op.add_column(
            table, sa.Column("package_id", sa.BigInteger(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table}_trade_id",
            table, "trade_structure",
            ["trade_id"], ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            f"fk_{table}_package_id",
            table, "package",
            ["package_id"], ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    for table in ("open_position_history", "open_position"):
        op.drop_constraint(f"fk_{table}_package_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_trade_id", table, type_="foreignkey")
        op.drop_column(table, "package_id")
        op.drop_column(table, "trade_id")
        op.drop_column(table, "contract_id")

    op.drop_constraint(
        "fk_trade_structure_package_id", "trade_structure", type_="foreignkey",
    )
    op.drop_column("trade_structure", "package_id")
    op.drop_table("package")
