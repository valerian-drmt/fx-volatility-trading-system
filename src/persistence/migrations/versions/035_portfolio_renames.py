"""Theme 2 — rename 6 portfolio tables to the *_history / open_/booked_ naming.

Pure ALTER TABLE … RENAME — data, FKs (OID-tracked) and constraint names all
follow automatically ; constraint names left unchanged (round-trip unbounded).

R10.1 Theme 2 (re-derived direct-to-final on the live 034 head — collapses the
r10 two-step path positions→position→open_position etc. into single targets).
Scope NARROWED vs the r10 theme-2 migration : the position_signal_tracking fold
(+8 signal cols on the metric table, backfill, drop source) is DEFERRED — that
table is still live on main (written by position_monitor, read by a positions
endpoint), so folding it is feature work (R10.2), not a rename. Its FK to
trade_positions is repointed to booked_position by the ORM change.

Renames :

  positions             -> open_position
  position_snapshots    -> open_position_history
  trade_positions       -> booked_position
  position_mtm_history  -> booked_position_metric_history
  account_snaps         -> account_history
  book_state_snapshots  -> book_state_snapshot_history

Revision ID: 035_portfolio_renames
Revises: 034_trade_order_renames
Create Date: 2026-06-14
"""
from __future__ import annotations

from alembic import op

revision: str = "035_portfolio_renames"
down_revision: str | None = "034_trade_order_renames"
branch_labels: str | None = None
depends_on: str | None = None


_RENAMES: tuple[tuple[str, str], ...] = (
    ("positions",            "open_position"),
    ("position_snapshots",   "open_position_history"),
    ("trade_positions",      "booked_position"),
    ("position_mtm_history", "booked_position_metric_history"),
    ("account_snaps",        "account_history"),
    ("book_state_snapshots", "book_state_snapshot_history"),
)


def upgrade() -> None:
    for old, new in _RENAMES:
        op.rename_table(old, new)


def downgrade() -> None:
    for old, new in reversed(_RENAMES):
        op.rename_table(new, old)
