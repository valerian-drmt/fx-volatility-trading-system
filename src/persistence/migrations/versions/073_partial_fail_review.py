"""The three trades left in ``partial_fail``, examined one by one (#494).

Read on prod on 2026-11-14 (read-only role). None of the three is a
multi-leg order with one leg out alone, which is what ``partial_fail`` is
for. Each is a ticket of ONE close, written on 2026-11-04 by the api's old
failure path (it stamped the ticket ``partial_fail`` and the order
``rejected`` when the engine did not answer, while the order could be live):

* trade 81, order 102: SELL 89 EUUF7 C1150, recorded ``rejected``
  ("execution-engine unreachable"), FILLED 89 of 89, one fill row;
* trade 82, order 103: BUY 100 EUUF7 C1180, same, FILLED 100 of 100;
* trade 78, order 97: SELL 10, ``cancelled``, nothing filled. The leg it was
  closing (order 67, nothing left open) still shows 10 reserved.

The fills of 102 and 103 are booked and their legs are flat: the book is
right, only the labels are wrong. This migration states what happened:

1. an order recorded as ended without a fill while its fill rows cover its
   whole quantity is ``filled``, with no refusal text;
2. a ``partial_fail`` trade is given the state its defining orders say
   (all filled: ``fully_filled``; none: ``fully_failed``; a real mix stays);
3. every leg's reservation is what its closes still in flight reserve.

General rules, not three hand-picked ids, and idempotent: a database where
the rows are already right is left untouched. Data only, so the downgrade
is a no-op.

Revision ID: 073_partial_fail_review
Revises: 072_idempotency_key
Create Date: 2026-11-14
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision: str = "073_partial_fail_review"
down_revision: str | None = "072_idempotency_key"
branch_labels: str | None = None
depends_on: str | None = None

logger = logging.getLogger("alembic.runtime.migration")

_ENDED = ("filled", "cancelled", "rejected", "expired")
_IN_FLIGHT = "('pending','submitted','partially_filled')"


def trade_state(order_states: list[str]) -> str | None:
    """State of a trade from the states of the orders that define it; None
    while one of them is still working or there is none. Pure, unit-tested."""
    if not order_states or any(s not in _ENDED for s in order_states):
        return None
    filled = sum(1 for s in order_states if s == "filled")
    if filled == len(order_states):
        return "fully_filled"
    return "fully_failed" if filled == 0 else "partial_fail"


def upgrade() -> None:
    bind = op.get_bind()

    relabelled = bind.execute(sa.text(
        "UPDATE trade_order SET state = 'filled', rejection_text = NULL, rejected_at = NULL "
        " WHERE state IN ('rejected','cancelled','expired') AND qty > 0 AND qty_filled >= qty "
        "   AND (SELECT COALESCE(SUM(f.qty_filled), 0) FROM trade_fill f WHERE f.order_id = trade_order.id) >= qty"
    )).rowcount

    rows = bind.execute(sa.text(
        "SELECT o.structure_id, o.order_role, o.state FROM trade_order o "
        "  JOIN trade_structure ts ON ts.id = o.structure_id "
        " WHERE ts.state = 'partial_fail'"
    )).all()
    by_trade: dict[int, list[tuple[str, str]]] = {}
    for sid, role, state in rows:
        by_trade.setdefault(int(sid), []).append((str(role), str(state)))
    restated = 0
    for sid, orders in sorted(by_trade.items()):
        roles = {r for r, _ in orders}
        primary = "entry" if "entry" in roles else ("closing" if "closing" in roles else None)
        if primary is None:
            continue
        new_state = trade_state([s for r, s in orders if r == primary])
        if new_state is None or new_state == "partial_fail":
            continue
        bind.execute(
            sa.text("UPDATE trade_structure SET state = :s, state_updated_at = CURRENT_TIMESTAMP WHERE id = :i"),
            {"s": new_state, "i": sid},
        )
        restated += 1

    reserved = (
        "COALESCE((SELECT SUM(CASE WHEN o.qty > o.qty_filled THEN o.qty - o.qty_filled ELSE 0 END) "
        "            FROM trade_order o "
        "           WHERE o.closes_order_id = leg_position.order_id AND o.order_role = 'closing' "
        f"             AND o.state IN {_IN_FLIGHT}), 0)"
    )
    released = bind.execute(sa.text(
        f"UPDATE leg_position SET reserved_qty = {reserved} WHERE reserved_qty <> {reserved}"
    )).rowcount

    logger.info(
        "073: %s order(s) relabelled filled, %s trade(s) restated from partial_fail, %s reservation(s) corrected",
        relabelled, restated, released,
    )


def downgrade() -> None:
    """Data only: the previous labels were wrong and are not restored."""
