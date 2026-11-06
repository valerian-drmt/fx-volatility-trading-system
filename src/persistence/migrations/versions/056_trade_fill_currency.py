"""``trade_fill.currency`` (#322, audit 05#4).

Every fill and commission was recorded without its currency: a 6E leg is
USD today, but IB's CommissionReport carries its own currency and nothing
kept it. One nullable column, additive; the fills handler fills it from the
CommissionReport (or the executed contract) from now on, and the
``commissionReportEvent`` handler corrects both commission and currency when
the report lands after the fill.

Revision ID: 056_trade_fill_currency
Revises: 055_submit_claim_cols
Create Date: 2026-11-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "056_trade_fill_currency"
down_revision: str | None = "055_submit_claim_cols"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("trade_fill", sa.Column("currency", sa.String(5), nullable=True))


def downgrade() -> None:
    op.drop_column("trade_fill", "currency")
