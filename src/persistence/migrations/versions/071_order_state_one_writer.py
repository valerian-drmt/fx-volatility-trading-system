"""An order's state has one writer: the cancel marker, and two dead states out.

1. ``trade_order.cancel_requested_at``: when the desk asked the broker to
   cancel the order. An order the broker ends without it was not cancelled
   by the desk: it expired (the end of the day for a DAY order). Until now
   the same event read ``cancelled`` from the status handler and ``expired``
   from the reaper (#485).
2. ``acknowledged`` (orders) and ``partial_fill`` (trades) were allowed by
   the constraints and listed everywhere, and nothing ever wrote them
   (#489). They leave the constraints. A row carrying one, if any exists, is
   first moved to the state it meant: ``submitted``.

Revision ID: 071_order_state_one_writer
Revises: 070_labels_and_notices
Create Date: 2026-11-14
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "071_order_state_one_writer"
down_revision: str | None = "070_labels_and_notices"
branch_labels: str | None = None
depends_on: str | None = None

_ORDER_NEW = "state IN ('pending','submitted','partially_filled','filled','rejected','cancelled','expired')"
_ORDER_OLD = (
    "state IN ('pending','submitted','acknowledged','partially_filled','filled',"
    "'rejected','cancelled','expired')"
)
_TRADE_NEW = "state IN ('submitted','fully_filled','partial_fail','fully_failed','closed')"
_TRADE_OLD = "state IN ('submitted','partial_fill','fully_filled','partial_fail','fully_failed','closed')"


def upgrade() -> None:
    op.add_column("trade_order", sa.Column("cancel_requested_at", sa.DateTime(timezone=True)))
    op.execute("UPDATE trade_order SET state = 'submitted' WHERE state = 'acknowledged'")
    op.execute("UPDATE trade_structure SET state = 'submitted' WHERE state = 'partial_fill'")
    with op.batch_alter_table("trade_order") as batch:
        batch.drop_constraint("ck_structure_orders_state", type_="check")
        batch.create_check_constraint("ck_structure_orders_state", _ORDER_NEW)
    with op.batch_alter_table("trade_structure") as batch:
        batch.drop_constraint("ck_trade_structures_state", type_="check")
        batch.create_check_constraint("ck_trade_structures_state", _TRADE_NEW)


def downgrade() -> None:
    with op.batch_alter_table("trade_structure") as batch:
        batch.drop_constraint("ck_trade_structures_state", type_="check")
        batch.create_check_constraint("ck_trade_structures_state", _TRADE_OLD)
    with op.batch_alter_table("trade_order") as batch:
        batch.drop_constraint("ck_structure_orders_state", type_="check")
        batch.create_check_constraint("ck_structure_orders_state", _ORDER_OLD)
    op.drop_column("trade_order", "cancel_requested_at")
