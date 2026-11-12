"""One-time repair of the book, so it starts in line with the broker (#468).

The code fixes of #462 to #465 keep the book right from now on. Three things
in the existing rows need a hand first; everything else heals by itself when
the execution engine starts (it rebuilds every leg with the netting rule,
then its lifecycle pass expires the legs on dead contracts and closes the
trades left flat).

1. Closing orders written before ``closes_order_id`` existed have no link, so
   they cannot reduce the leg they closed. Each is linked to the oldest entry
   leg on the same contract, opposite side, that still has the quantity. A
   close that fits no leg is left alone (it keeps its own quantity, so the
   per-contract sum stays right).
2. An order marked filled with fewer recorded executions than its filled
   quantity gets a ``repair`` position event for the difference (prod: order
   108 of trade 87, filled 10, zero fills).
3. A ``booked_position`` closed by the old mirror check
   (``reconciled_flat_at_ib``) while the broker still holds one of its legs
   is reopened (prod: trades 60 and 88). The lifecycle pass closes it again,
   for the right reason, if the book says it is flat.

Idempotent: each step only touches rows still in the faulty state. The counts
are logged. Data only, no schema change, so the downgrade is a no-op: the
previous state was wrong and is not worth restoring.

Revision ID: 067_book_repair
Revises: 066_instrument
Create Date: 2026-11-12
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision: str = "067_book_repair"
down_revision: str | None = "066_instrument"
branch_labels: str | None = None
depends_on: str | None = None

logger = logging.getLogger("alembic.runtime.migration")


def link_closes(entries: list[dict], closes: list[dict]) -> dict[int, int]:
    """``{closing order id: entry order id}`` for the unlinked closes.

    ``entries``: id, symbol, side, filled, closed (already linked quantity).
    ``closes``: id, symbol, side, filled. First fit, oldest entry first, only
    an entry older than the close and with the whole quantity left. Pure, so
    it is unit-tested without a database."""
    left = {int(e["id"]): float(e["filled"]) - float(e["closed"]) for e in entries}
    by_symbol: dict[str, list[dict]] = {}
    for e in sorted(entries, key=lambda e: int(e["id"])):
        by_symbol.setdefault(str(e["symbol"]), []).append(e)
    out: dict[int, int] = {}
    for c in sorted(closes, key=lambda c: int(c["id"])):
        qty = float(c["filled"])
        if qty <= 0:
            continue
        for e in by_symbol.get(str(c["symbol"]), []):
            if int(e["id"]) >= int(c["id"]) or e["side"] == c["side"]:
                continue
            if left[int(e["id"])] + 1e-9 >= qty:
                out[int(c["id"])] = int(e["id"])
                left[int(e["id"])] -= qty
                break
    return out


def upgrade() -> None:
    bind = op.get_bind()

    # 1. link the closes that predate closes_order_id
    entries = [dict(r._mapping) for r in bind.execute(sa.text(
        "SELECT o.id, o.ib_local_symbol AS symbol, o.side, o.qty_filled AS filled, "
        "       COALESCE((SELECT sum(c.qty_filled) FROM trade_order c "
        "                  WHERE c.closes_order_id = o.id AND c.order_role = 'closing'), 0) AS closed "
        "  FROM trade_order o "
        " WHERE o.order_role = 'entry' AND o.qty_filled > 0 AND o.ib_local_symbol IS NOT NULL"
    ))]
    closes = [dict(r._mapping) for r in bind.execute(sa.text(
        "SELECT id, ib_local_symbol AS symbol, side, qty_filled AS filled FROM trade_order "
        " WHERE order_role = 'closing' AND closes_order_id IS NULL "
        "   AND qty_filled > 0 AND ib_local_symbol IS NOT NULL"
    ))]
    links = link_closes(entries, closes)
    for close_id, entry_id in links.items():
        bind.execute(
            sa.text("UPDATE trade_order SET closes_order_id = :e WHERE id = :c AND closes_order_id IS NULL"),
            {"e": entry_id, "c": close_id},
        )

    # 1b. a trade closed although one of its filled entry legs has no recorded
    #     execution was closed on no evidence (trade 87 on prod, closed by the
    #     first lifecycle pass of the release before this one): reopen it, and
    #     its booked position, before step 2 gives the leg its quantity.
    no_evidence = (
        "SELECT o.structure_id FROM trade_order o "
        " WHERE o.order_role = 'entry' AND o.state = 'filled' "
        "   AND o.qty_filled > COALESCE((SELECT sum(f.qty_filled) FROM trade_fill f WHERE f.order_id = o.id), 0) "
        "   AND NOT EXISTS (SELECT 1 FROM position_event e WHERE e.order_id = o.id)"
    )
    bind.execute(sa.text(
        "UPDATE booked_position SET state = 'open', closed_at = NULL, close_reason = NULL, "
        "       exit_total_cost_usd = NULL, gross_pnl_usd = NULL, net_pnl_usd = NULL, "
        "       state_updated_at = CURRENT_TIMESTAMP "
        f" WHERE state IN ('closed', 'expired') AND structure_id IN ({no_evidence})"
    ))
    unclosed = bind.execute(sa.text(
        "UPDATE trade_structure SET state = 'fully_filled', closed_at = NULL, close_reason = NULL, "
        "       state_updated_at = CURRENT_TIMESTAMP "
        f" WHERE state = 'closed' AND id IN ({no_evidence})"
    )).rowcount

    # 2. a filled order with missing executions gets its quantity
    repaired = bind.execute(sa.text(
        "INSERT INTO position_event (order_id, kind, side, qty, price, occurred_at, dedup_key, source, note) "
        "SELECT o.id, 'repair', o.side, "
        "       o.qty_filled - COALESCE((SELECT sum(f.qty_filled) FROM trade_fill f WHERE f.order_id = o.id), 0), "
        "       COALESCE(o.avg_fill_price, 0), COALESCE(o.fully_filled_at, o.state_updated_at), "
        "       'missing_fills', 'migration_067', "
        "       'order filled at the broker, executions never received; price is an estimate' "
        "  FROM trade_order o "
        " WHERE o.state = 'filled' "
        "   AND o.qty_filled > COALESCE((SELECT sum(f.qty_filled) FROM trade_fill f WHERE f.order_id = o.id), 0) "
        "   AND NOT EXISTS (SELECT 1 FROM position_event e "
        "                    WHERE e.order_id = o.id AND e.kind = 'repair' AND e.dedup_key = 'missing_fills')"
    )).rowcount

    # 3. reopen the booked trades the mirror check closed while still held
    reopened = bind.execute(sa.text(
        "UPDATE booked_position SET state = 'open', closed_at = NULL, close_reason = NULL, "
        "       state_updated_at = CURRENT_TIMESTAMP "
        " WHERE state = 'closed' AND close_reason = 'reconciled_flat_at_ib' "
        "   AND EXISTS (SELECT 1 FROM trade_order o "
        "                 JOIN open_position p ON p.structure = o.ib_local_symbol AND p.side = o.side "
        "                WHERE o.structure_id = booked_position.structure_id AND o.order_role = 'entry')"
    )).rowcount

    logger.info(
        "book_repair closes_linked=%s of %s unlinked, trades_unclosed=%s, repair_events=%s, booked_reopened=%s",
        len(links), len(closes), unclosed, repaired, reopened,
    )


def downgrade() -> None:
    # Data repair: nothing to undo (see the module docstring).
    pass
