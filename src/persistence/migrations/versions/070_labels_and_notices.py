"""Every trade reads as a product; a filled order keeps no refusal text.

Two kinds of rows on prod were built differently from their neighbours:

1. Trades opened from the ticket carry the classifier's name ("long put",
   "short straddle", "risk reversal 25d") and had NO ``product_label``,
   while trades written by a close carry a template code ("vanilla_put")
   and the label "Vanilla Put". 54 trades without a label on 2026-11-13.
   ``core.products.product_label_from_symbol`` now reads both vocabularies;
   this fills the label where it is missing.
2. Orders that FILLED still show IB's notice 10349 ("Order TIF was set to
   DAY...") as their refusal text. They were not refused.

Idempotent: only rows still in the faulty state are touched. Data only, so
the downgrade is a no-op.

Revision ID: 070_labels_and_notices
Revises: 069_order_rows_cleanup
Create Date: 2026-11-13
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

from core.products import product_label_from_symbol

revision: str = "070_labels_and_notices"
down_revision: str | None = "069_order_rows_cleanup"
branch_labels: str | None = None
depends_on: str | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()

    types = bind.execute(sa.text(
        "SELECT DISTINCT structure_type FROM trade_structure "
        " WHERE product_label IS NULL AND structure_type IS NOT NULL"
    )).scalars().all()
    labelled = 0
    for structure_type in types:
        label = product_label_from_symbol(None, str(structure_type))
        if label is None:
            continue
        labelled += bind.execute(
            sa.text("UPDATE trade_structure SET product_label = :l "
                    " WHERE product_label IS NULL AND structure_type = :t"),
            {"l": label, "t": structure_type},
        ).rowcount

    cleared = bind.execute(sa.text(
        "UPDATE trade_order SET rejection_text = NULL "
        " WHERE state = 'filled' AND rejection_text IS NOT NULL"
    )).rowcount

    logger.info("labels_and_notices trades_labelled=%s filled_orders_cleared=%s", labelled, cleared)


def downgrade() -> None:
    # Data repair: nothing to undo (see the module docstring).
    pass
