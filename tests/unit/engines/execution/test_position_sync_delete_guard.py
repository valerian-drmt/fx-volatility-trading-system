"""position_sync untrusted-snapshot delete guard (#317, audit 03#11).

The mirror (``open_position``) must never be flattened by an IB snapshot that
cannot be trusted: a connected-but-dead feed (no account values) or a transient
empty ``positions()`` while the DB still holds rows. A genuinely flat,
reporting account does delete. Fakes only; no IB, no Redis.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


class _Exec:
    def __init__(self, *, connected: bool = True, reporting: bool = True, positions: list[dict[str, Any]] | None = None):
        self._connected, self._reporting, self._positions = connected, reporting, positions or []

    def is_connected(self) -> bool:
        return self._connected

    def account_is_reporting(self) -> bool:
        return self._reporting

    async def list_positions(self) -> list[dict[str, Any]]:
        return list(self._positions)


async def _seed(maker, *symbols: str) -> None:
    from persistence.models import OpenPosition
    async with maker() as db:
        for s in symbols:
            db.add(OpenPosition(structure=s, side="BUY", quantity=Decimal("10"),
                                entry_timestamp=datetime.now(UTC)))
        await db.commit()


async def _rows(maker) -> list[str]:
    from sqlalchemy import select

    from persistence.models import OpenPosition
    async with maker() as db:
        return sorted((await db.execute(select(OpenPosition.structure))).scalars().all())


async def test_empty_snapshot_from_a_non_reporting_account_deletes_nothing(sqlite_sessionmaker: Any) -> None:
    from engines.execution.position_sync import sync_positions_from_ib

    await _seed(sqlite_sessionmaker, "EUUZ6 C1130", "6EZ6")
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, _Exec(reporting=False, positions=[]))  # type: ignore[arg-type]
    assert out["closed"] == 0 and out["deletes_skipped"] is True
    assert await _rows(sqlite_sessionmaker) == ["6EZ6", "EUUZ6 C1130"]


async def test_empty_snapshot_while_rows_exist_is_untrusted_even_when_reporting(sqlite_sessionmaker: Any) -> None:
    """The second half of the guard: ``not (not ib_active and db_by_key)``."""
    from engines.execution.position_sync import sync_positions_from_ib

    await _seed(sqlite_sessionmaker, "EUUZ6 C1130")
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, _Exec(reporting=True, positions=[]))  # type: ignore[arg-type]
    assert out["closed"] == 0 and out["deletes_skipped"] is True
    assert await _rows(sqlite_sessionmaker) == ["EUUZ6 C1130"]


async def test_disconnected_executor_touches_nothing(sqlite_sessionmaker: Any) -> None:
    from engines.execution.position_sync import sync_positions_from_ib

    await _seed(sqlite_sessionmaker, "EUUZ6 C1130")
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, _Exec(connected=False))  # type: ignore[arg-type]
    assert out.get("error") == "ib_not_connected"
    assert await _rows(sqlite_sessionmaker) == ["EUUZ6 C1130"]


async def test_trusted_partial_snapshot_closes_only_the_missing_row(sqlite_sessionmaker: Any) -> None:
    """Reporting account, one of two rows gone at IB -> that one is deleted."""
    from engines.execution.position_sync import sync_positions_from_ib

    await _seed(sqlite_sessionmaker, "EUUZ6 C1130", "6EZ6")
    still_open = [{"local_symbol": "6EZ6", "symbol": "EUR", "position": 7.0, "avg_cost": 140_000.0,
                   "expiry": "20261214", "con_id": 1}]
    async with sqlite_sessionmaker() as db:
        out = await sync_positions_from_ib(db, _Exec(reporting=True, positions=still_open))  # type: ignore[arg-type]
    assert out["closed"] == 1 and out["deletes_skipped"] is False
    assert await _rows(sqlite_sessionmaker) == ["6EZ6"]
