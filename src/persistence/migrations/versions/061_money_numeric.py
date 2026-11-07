"""Execution money and prices as NUMERIC (#326, audit 05#8).

The ledger tables (account_history, open_position, …) store money as
``Numeric`` while every execution-side table stored prices, premiums,
commissions and P&L as ``double precision``: a fill price of 0.01151105
and a commission of 2.37 were binary floats, and sums across fills drifted
in the last digits. One policy now (``docs/architecture/database.md``):

* prices / strikes / spots / IVs: ``NUMERIC(18, 8)``
* USD amounts (premium, commission, slippage, cost, P&L): ``NUMERIC(15, 2)``
* risk metrics that are not money (greeks, thresholds, z-scores): unchanged ``Float``

The ORM maps these through ``persistence.models.MoneyNumeric``: exact in
the database, bound from a float or a Decimal, read back as a float so the
existing call sites and payloads are untouched. ``ALTER COLUMN TYPE ...
USING col::numeric`` rewrites the rows (the execution tables are small: a
few thousand fills at most).

Revision ID: 061_money_numeric
Revises: 060_schema_uniques
Create Date: 2026-11-07
"""
from __future__ import annotations

from alembic import op

revision: str = "061_money_numeric"
down_revision: str | None = "060_schema_uniques"
branch_labels: str | None = None
depends_on: str | None = None

USD = "NUMERIC(15, 2)"
PRICE = "NUMERIC(18, 8)"
PCT = "NUMERIC(10, 4)"

COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("trade_structure", "total_premium_paid_usd", USD),
    ("trade_structure", "total_slippage_usd", USD),
    ("trade_structure", "total_commission_usd", USD),
    ("trade_structure", "total_entry_cost_usd", USD),
    ("trade_order", "contract_strike", PRICE),
    ("trade_order", "limit_price", PRICE),
    ("trade_order", "preview_iv_pct", PCT),
    ("trade_order", "preview_price", PRICE),
    ("trade_order", "avg_fill_price", PRICE),
    ("trade_order", "total_commission_usd", USD),
    ("trade_order", "slippage_per_contract", PRICE),
    ("trade_order", "total_slippage_usd", USD),
    ("trade_fill", "fill_price", PRICE),
    ("trade_fill", "commission_usd", USD),
    ("trade_fill", "spot_at_fill", PRICE),
    ("trade_fill", "bid_at_fill", PRICE),
    ("trade_fill", "ask_at_fill", PRICE),
    ("trade_fill", "iv_implied_from_fill", PCT),
    ("booked_position", "entry_premium_usd", USD),
    ("booked_position", "entry_total_cost_usd", USD),
    ("booked_position", "entry_spot", PRICE),
    ("booked_position", "entry_iv_avg", PCT),
    ("booked_position", "exit_premium_usd", USD),
    ("booked_position", "exit_total_cost_usd", USD),
    ("booked_position", "gross_pnl_usd", USD),
    ("booked_position", "net_pnl_usd", USD),
    ("spot_order", "limit_price", PRICE),
    ("spot_order", "avg_fill_price", PRICE),
    ("spot_order", "commission_usd", USD),
    ("hedge_order", "fill_price", PRICE),
    ("hedge_order", "commission_usd", USD),
    ("hedge_order", "spread_paid_usd", USD),
    ("hedge_order", "total_cost_usd", USD),
)


def upgrade() -> None:
    for table, column, kind in COLUMNS:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {kind} USING {column}::{kind}")


def downgrade() -> None:
    for table, column, _kind in COLUMNS:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE DOUBLE PRECISION "
            f"USING {column}::DOUBLE PRECISION"
        )
