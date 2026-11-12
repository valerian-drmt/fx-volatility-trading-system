"""position_event accepts the kind ``writeoff``.

A leg on a contract the broker does not hold is an error in the book. The
lifecycle pass corrects it with a ``writeoff`` event (audited, no P&L
invented) instead of leaving a "book only" line on the panels for ever.

Revision ID: 068_event_writeoff
Revises: 067_book_repair
Create Date: 2026-11-12
"""
from __future__ import annotations

from alembic import op

revision: str = "068_event_writeoff"
down_revision: str | None = "067_book_repair"
branch_labels: str | None = None
depends_on: str | None = None

_NAME = "ck_position_event_kind"
_NEW = "kind IN ('expiry','exercise','assignment','repair','writeoff')"
_OLD = "kind IN ('expiry','exercise','assignment','repair')"


def upgrade() -> None:
    op.drop_constraint(_NAME, "position_event", type_="check")
    op.create_check_constraint(_NAME, "position_event", _NEW)


def downgrade() -> None:
    # The write-offs are the audit trail of corrections already applied to
    # leg_position; relabel them so the narrower constraint accepts the rows.
    op.execute("UPDATE position_event SET kind = 'expiry' WHERE kind = 'writeoff'")
    op.drop_constraint(_NAME, "position_event", type_="check")
    op.create_check_constraint(_NAME, "position_event", _OLD)
