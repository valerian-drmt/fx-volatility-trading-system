"""Unit tests for engines.execution.ib_heartbeat.

Cover the persistence-layer behaviours :
  * `update_heartbeat_row` writes/upserts the singleton, flips fields,
    and stamps `last_disconnect_at` on a connected → disconnected edge.
  * `find_stuck_orders` returns only `submitted/acknowledged` orders past
    the cutoff, ignoring filled/cancelled ones.
  * `_already_alerted_recently` (via `stuck_order_watcher_loop` semantics)
    dedups by `order_id` within the window.

Uses an in-memory aiosqlite DB so the test stays self-contained — no
docker-compose, no Postgres.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


def _coerce_bigint_to_integer(metadata) -> None:
    """SQLite ROWID autoincrement only fires on INTEGER PRIMARY KEY columns ;
    BigInteger maps to BIGINT and breaks INSERT-without-id. Swap in-place
    for the duration of the test (we own the metadata clone here)."""
    from sqlalchemy import BigInteger, Integer
    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, BigInteger):
                col.type = Integer()


async def _make_session():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import Base

    _coerce_bigint_to_integer(Base.metadata)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    return maker, engine


async def test_update_heartbeat_row_inserts_when_missing():
    from sqlalchemy import select

    from engines.execution.ib_heartbeat import update_heartbeat_row
    from persistence.models import IbConnectionState

    maker, engine = await _make_session()
    try:
        now = datetime.now(UTC)
        async with maker() as db:
            await update_heartbeat_row(
                db, is_connected=True,
                account_summary={"account": "DU123", "AvailableFunds": 10000,
                                 "BuyingPower": 50000, "MaintMarginReq": 1000},
                now=now,
            )
            await db.commit()
        async with maker() as db:
            row = (await db.execute(
                select(IbConnectionState).where(IbConnectionState.broker == "IB")
            )).scalar_one()
        assert row.is_connected is True
        assert row.account_id == "DU123"
        assert row.available_funds_usd == pytest.approx(10000.0)
        assert row.buying_power_usd == pytest.approx(50000.0)
        assert row.margin_used_usd == pytest.approx(1000.0)
    finally:
        await engine.dispose()


async def test_update_heartbeat_row_marks_disconnect_on_edge():
    from sqlalchemy import select

    from engines.execution.ib_heartbeat import update_heartbeat_row
    from persistence.models import IbConnectionState

    maker, engine = await _make_session()
    try:
        t0 = datetime.now(UTC)
        async with maker() as db:
            await update_heartbeat_row(
                db, is_connected=True,
                account_summary={"AvailableFunds": 1.0}, now=t0,
            )
            await db.commit()
        # Now simulate a disconnect tick.
        t1 = t0 + timedelta(seconds=10)
        async with maker() as db:
            await update_heartbeat_row(
                db, is_connected=False, account_summary=None, now=t1,
            )
            await db.commit()
        async with maker() as db:
            row = (await db.execute(
                select(IbConnectionState).where(IbConnectionState.broker == "IB")
            )).scalar_one()
        assert row.is_connected is False
        assert row.last_disconnect_at is not None
        # tz-aware compare ; sqlite drops tzinfo so coerce both sides.
        last_disc = row.last_disconnect_at.replace(tzinfo=row.last_disconnect_at.tzinfo or UTC)
        assert last_disc.replace(tzinfo=None) == t1.replace(tzinfo=None)
        assert row.n_disconnects_24h == 1
    finally:
        await engine.dispose()


async def test_find_stuck_orders_filters_by_state_and_age():
    from engines.execution.ib_heartbeat import find_stuck_orders
    from persistence.models import StructureOrder, TradeStructure

    maker, engine = await _make_session()
    try:
        now = datetime.now(UTC)
        old = now - timedelta(minutes=15)        # > 10min cutoff
        recent = now - timedelta(minutes=2)

        async with maker() as db:
            struct = TradeStructure(
                id=1,
                structure_type="straddle", reference_tenor="3M",
                base_qty=1, state="submitted", execution_mode="live",
            )
            db.add(struct)
            await db.flush()

            # Stuck (acknowledged for 15 min)
            db.add(StructureOrder(
                id=1, structure_id=struct.id, leg_idx=0,
                contract_type="call", side="BUY", qty=1, order_type="LMT",
                limit_price=1.0, state="acknowledged", submitted_at=old,
            ))
            # Stuck (submitted, no ack yet, 15 min)
            db.add(StructureOrder(
                id=2, structure_id=struct.id, leg_idx=1,
                contract_type="put", side="BUY", qty=1, order_type="LMT",
                limit_price=1.0, state="submitted", submitted_at=old,
            ))
            # Recent acknowledged (should NOT trigger)
            db.add(StructureOrder(
                id=3, structure_id=struct.id, leg_idx=2,
                contract_type="call", side="SELL", qty=1, order_type="LMT",
                limit_price=1.0, state="acknowledged", submitted_at=recent,
            ))
            # Filled (should NOT trigger regardless of age)
            db.add(StructureOrder(
                id=4, structure_id=struct.id, leg_idx=3,
                contract_type="put", side="SELL", qty=1, order_type="LMT",
                limit_price=1.0, state="filled", submitted_at=old,
            ))
            await db.commit()

        async with maker() as db:
            stuck = await find_stuck_orders(
                db, now=now, stuck_after_seconds=600.0,
            )
        states = sorted(o.state for o in stuck)
        assert states == ["acknowledged", "submitted"]
        # sqlite drops tzinfo ; coerce both sides to naive for the comparison.
        cutoff_naive = (now - timedelta(seconds=600)).replace(tzinfo=None)
        assert all(o.submitted_at.replace(tzinfo=None) < cutoff_naive for o in stuck)
    finally:
        await engine.dispose()


async def test_already_alerted_recently_dedupes_by_order_id():
    from engines.execution.ib_heartbeat import _already_alerted_recently
    from persistence.models import (
        StructureOrder,
        TradeEvent,
        TradeStructure,
    )

    maker, engine = await _make_session()
    try:
        now = datetime.now(UTC)
        async with maker() as db:
            struct = TradeStructure(
                id=1,
                structure_type="straddle", reference_tenor="3M",
                base_qty=1, state="submitted", execution_mode="live",
            )
            db.add(struct)
            await db.flush()
            order = StructureOrder(
                id=1, structure_id=struct.id, leg_idx=0,
                contract_type="call", side="BUY", qty=1, order_type="LMT",
                limit_price=1.0, state="acknowledged",
                submitted_at=now - timedelta(minutes=20),
            )
            db.add(order)
            await db.flush()
            db.add(TradeEvent(
                id=1, structure_id=struct.id, order_id=order.id,
                event_type="stuck_order_alert", severity="critical",
                description="seeded", payload={"order_id": order.id},
                ts=now - timedelta(minutes=2),
            ))
            await db.commit()
            order_id_kept = order.id

        async with maker() as db:
            assert await _already_alerted_recently(
                db, order_id_kept, now, dedup_window_s=600.0,
            ) is True
            # Order with no prior alert → not deduped
            assert await _already_alerted_recently(
                db, 999999, now, dedup_window_s=600.0,
            ) is False
    finally:
        await engine.dispose()
