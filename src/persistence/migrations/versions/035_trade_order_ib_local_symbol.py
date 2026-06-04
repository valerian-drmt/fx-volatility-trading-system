"""Add ``trade_order.ib_local_symbol`` to record the actual IB contract.

Why : the leg→trade map in ``position_sync`` previously had to
reconstruct the IB ``localSymbol`` from the leg's calibrated strike
(e.g. 1.16641 → guess 1.165 / 1.166 / 1.170). When two trades have
strikes that collide on the standard 0.005 tick (e.g. an older straddle
at strike 1.166 and a newer strangle whose put leg also rounds to 1.165),
the candidate-emission approach over-claims and misattributes legs.

Storing the **actual** IB ``localSymbol`` returned by the fill event
eliminates the ambiguity : the map keys become exact, no fuzzy matching
needed. Populated by ``fills_handler._on_execution`` on the first fill
of each leg (further fills do not overwrite).

Nullable for now : legs filled before this migration won't have it
backfilled — they'll fall back to the reconstruction path until they
close. Promote to NOT NULL in a follow-up once writer coverage is proven
and old fills have aged out.

Revision ID: 035_trade_order_ib_local_symbol
Revises: 034_package_trade_contract_ids
Create Date: 2026-06-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "035_trade_order_ib_local_symbol"
down_revision: str | None = "034_package_trade_contract_ids"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "trade_order",
        sa.Column("ib_local_symbol", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_order", "ib_local_symbol")
