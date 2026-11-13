"""Clean the order rows the old defects left behind (#494).

Two kinds of rows on prod say something that is not true:

1. Trades still ``submitted`` although every order that defines them has
   ended (cancelled, rejected, expired): the status handler used to update
   the order and nothing updated the trade. Eleven on prod, the oldest from
   July. Each gets the state its orders give it: ``fully_filled`` when all
   filled, ``fully_failed`` when none did, ``partial_fail`` otherwise.
2. Closes recorded ``rejected`` on IB's notice 10349 while the order was live
   at the broker. They were never rejected: they waited and expired unfilled
   at the end of the day. Their state and reason say so.

From now on ``persistence.order_state.end_order`` keeps both true. Idempotent:
only rows still in the faulty state are touched. Data only, so the downgrade
is a no-op.

Revision ID: 069_order_rows_cleanup
Revises: 068_event_writeoff
Create Date: 2026-11-13
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision: str = "069_order_rows_cleanup"
down_revision: str | None = "068_event_writeoff"
branch_labels: str | None = None
depends_on: str | None = None

logger = logging.getLogger("alembic.runtime.migration")

_ENDED = ("filled", "cancelled", "rejected", "expired")


def final_state(order_states: list[str]) -> str | None:
    """State of a trade from the states of the orders that define it; None
    while one of them is still working. Pure, unit-tested."""
    if not order_states or any(s not in _ENDED for s in order_states):
        return None
    filled = sum(1 for s in order_states if s == "filled")
    if filled == len(order_states):
        return "fully_filled"
    return "fully_failed" if filled == 0 else "partial_fail"


def upgrade() -> None:
    bind = op.get_bind()

    # 2 first, so the trades of those closes are judged on the corrected state
    relabelled = bind.execute(sa.text(
        "UPDATE trade_order SET state = 'expired', "
        "       rejection_text = 'expired unfilled at the end of the day "
        "(was recorded rejected on IB notice 10349 while live)' "
        " WHERE state = 'rejected' AND rejection_text LIKE 'Error 10349%'"
    )).rowcount

    rows = bind.execute(sa.text(
        "SELECT o.structure_id, o.order_role, o.state FROM trade_order o "
        "  JOIN trade_structure ts ON ts.id = o.structure_id "
        " WHERE ts.state = 'submitted'"
    )).all()
    by_trade: dict[int, list[tuple[str, str]]] = {}
    for sid, role, state in rows:
        by_trade.setdefault(int(sid), []).append((str(role), str(state)))
    finalised = 0
    for sid, orders in sorted(by_trade.items()):
        roles = {r for r, _ in orders}
        primary = "entry" if "entry" in roles else ("closing" if "closing" in roles else None)
        if primary is None:
            continue
        new_state = final_state([s for r, s in orders if r == primary])
        if new_state is None:
            continue
        bind.execute(
            sa.text("UPDATE trade_structure SET state = :s, state_updated_at = CURRENT_TIMESTAMP "
                    " WHERE id = :i AND state = 'submitted'"),
            {"s": new_state, "i": sid},
        )
        finalised += 1

    logger.info("order_rows_cleanup closes_relabelled=%s trades_finalised=%s", relabelled, finalised)


def downgrade() -> None:
    # Data repair: nothing to undo (see the module docstring).
    pass
