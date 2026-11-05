"""`_underlying_forward` — the book-level 6E forward (now / t-1) behind
`/portfolio/pnl-attribution`.

Prod incident 2026-11-05: with no 6E future open, the t-1 lookup was an
unanchored ``ORDER BY timestamp DESC LIMIT 1`` over the whole
``open_position_history`` joined on ``structure LIKE '6E%'`` — the join never
matched, LIMIT 1 never stopped the backward walk, and every call hit the 20s
statement timeout (500). The UI fetches attribution in the same bundle as
``/portfolio/account``, so the Cash & margin panel fell back to the trade-book
bootstrap mock ($100k, zero margin) while Cash holdings showed real IB cash.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from api.routers.portfolio_panel import _underlying_forward

CUTOFF = datetime(2026, 11, 4, 9, 0, tzinfo=UTC) - timedelta(hours=24)


def _db(*firsts: tuple | None) -> AsyncMock:
    """Fake AsyncSession whose successive ``execute(...).first()`` return *firsts*."""
    results = []
    for f in firsts:
        r = MagicMock()
        r.first.return_value = f
        results.append(r)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=results)
    return db


@pytest.mark.asyncio
async def test_no_future_open_skips_the_history_probe():
    db = _db(None)
    assert await _underlying_forward(db, CUTOFF) == (None, None)
    # ONE query only: no unanchored walk over open_position_history.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_future_open_probes_history_anchored_on_position_id():
    db = _db((1.1241,), (1.1198,))
    assert await _underlying_forward(db, CUTOFF) == (pytest.approx(1.1241), pytest.approx(1.1198))
    assert db.execute.await_count == 2
    sql = str(db.execute.await_args_list[1].args[0])
    # Per-position LATERAL on the (position_id, timestamp) index — never a
    # whole-table ORDER BY timestamp with the 6E filter applied via the join.
    assert "LATERAL" in sql
    assert "h.position_id = op.id" in sql
    assert db.execute.await_args_list[1].args[1] == {"cutoff": CUTOFF}


@pytest.mark.asyncio
async def test_future_open_but_no_t1_snapshot_yet():
    db = _db((1.1241,), None)
    assert await _underlying_forward(db, CUTOFF) == (pytest.approx(1.1241), None)
