"""Theme 3 — rename 6 trade/order tables to the trade_* / *_order singular naming.

Pure ALTER TABLE … RENAME — data, FKs (tracked by OID in Postgres) and
constraint names all follow automatically ; constraint names are left
unchanged (still encode the old table name) so the migration stays a clean,
fully-reversible rename. Round-trip unbounded.

R10.1 Theme 3 (re-derived on the live 033 head). Scope NARROWED vs the r10
theme-3 migration : the dead-table drops (trades/orders/order_events) and the
execution_audit_log → trade_event fold are DEFERRED — on main those tables are
still live (position_sync / execution.main use Order/Trade/OrderEvent ; audit
log written from ~12 sites), so removing them is feature work (R10.2), not a
rename. structure_definitions is left alone (it is dropped in PR 1.5).

Renames :

  trade_previews    -> trade_preview
  trade_structures  -> trade_structure
  structure_orders  -> trade_order
  structure_fills   -> trade_fill
  hedge_orders      -> hedge_order
  exit_alerts       -> exit_alert

Revision ID: 034_trade_order_renames
Revises: 033_config_scalar_ib_rename
Create Date: 2026-06-14
"""
from __future__ import annotations

from alembic import op

revision: str = "034_trade_order_renames"
down_revision: str | None = "033_config_scalar_ib_rename"
branch_labels: str | None = None
depends_on: str | None = None


# (old, new) — applied in order on upgrade, reversed on downgrade.
_RENAMES: tuple[tuple[str, str], ...] = (
    ("trade_previews",   "trade_preview"),
    ("trade_structures", "trade_structure"),
    ("structure_orders", "trade_order"),
    ("structure_fills",  "trade_fill"),
    ("hedge_orders",     "hedge_order"),
    ("exit_alerts",      "exit_alert"),
)


def upgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(old, new)


def downgrade() -> None:
    for old, new in reversed(_RENAMES):
        op.rename_table(new, old)
