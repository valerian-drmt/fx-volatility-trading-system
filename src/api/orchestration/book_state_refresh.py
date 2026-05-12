"""Recompute the singleton ``book_state_snapshots`` row after a structure
transitions to ``fully_filled`` or ``closed``.

Today the row is bootstrapped at zero (cf. ``trade.py:_load_book``) and never
refreshed — every sizing call therefore sees ``total_vega_usd = 0`` and the
``book_alpha`` penalty is effectively dead. This module fills that gap.

Strategy : we sum entry-time greeks across all open ``trade_positions`` (no
re-pricing — that's STEP5 territory). The previous current row is flipped to
``is_current=False`` (becomes part of history) and a new row is inserted with
``is_current=True``.

Caller is responsible for committing the session.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import BookStateSnapshot, BookedPosition, TradeStructure

logger = logging.getLogger(__name__)


async def refresh_book_state(
    db: AsyncSession, *, symbol: str = "EURUSD",
    capital_default: float = 100_000.0,
) -> BookStateSnapshot:
    """Recompute and persist a fresh book-state snapshot. Returns the new row."""
    # 1. Load all currently-open positions joined to their parent structures.
    rows = (await db.execute(
        select(BookedPosition, TradeStructure)
        .join(TradeStructure, TradeStructure.id == BookedPosition.structure_id)
        .where(BookedPosition.state == "open")
    )).all()

    total_vega = 0.0
    total_gamma = 0.0
    total_theta = 0.0
    n_structures = 0
    n_legs = 0
    notional = 0.0
    structure_ids: set[int] = set()

    for pos, struct in rows:
        total_vega += float(pos.entry_vega_usd_per_volpt or 0.0)
        total_gamma += float(pos.entry_gamma_usd_per_pip2 or 0.0)
        total_theta += float(pos.entry_theta_usd_per_day or 0.0)
        notional += float(pos.entry_premium_usd or 0.0)
        structure_ids.add(struct.id)
        # n_legs not tracked at position level — count from structure orders if needed.

    n_structures = len(structure_ids)
    n_legs = n_structures  # conservative placeholder ; full leg count = JOIN cost we skip here

    # 2. Carry capital_total from the previous current row, else default.
    prev = (await db.execute(
        select(BookStateSnapshot)
        .where(BookStateSnapshot.symbol == symbol)
        .where(BookStateSnapshot.is_current.is_(True))
        .limit(1)
    )).scalar_one_or_none()
    capital_total = float(prev.capital_total_usd) if (
        prev is not None and prev.capital_total_usd is not None
    ) else capital_default

    # 3. Flip previous to historical, insert new current.
    await db.execute(
        update(BookStateSnapshot)
        .where(BookStateSnapshot.symbol == symbol)
        .where(BookStateSnapshot.is_current.is_(True))
        .values(is_current=False)
    )
    new_row = BookStateSnapshot(
        timestamp=datetime.now(UTC),
        symbol=symbol,
        total_vega_usd=round(total_vega, 4),
        total_gamma_usd=round(total_gamma, 4),
        total_theta_usd=round(total_theta, 4),
        total_delta=0.0,
        n_open_structures=n_structures,
        n_open_legs=n_legs,
        notional_engaged_usd=round(notional, 2),
        capital_total_usd=capital_total,
        margin_used_usd=prev.margin_used_usd if prev is not None else 0.0,
        is_current=True,
    )
    db.add(new_row)
    await db.flush()
    return new_row
