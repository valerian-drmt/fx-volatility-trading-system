"""Unit tests for engines.execution.fills_handler.

Verifies the persistence behaviour of the two ib_insync callbacks :
  * `_on_order_status` flips DB state on Submitted / Cancelled / Inactive.
  * `_on_execution` writes a fill row, dedupes on ib_execution_id, recomputes
    aggregates, and triggers the structure-completion cascade
    (`maybe_complete_structure`) when all legs are filled.

Uses an in-memory aiosqlite DB ; the ib_insync `Trade`/`Fill` objects are
SimpleNamespaces.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


def _coerce_bigint_to_integer(metadata) -> None:
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
    return async_sessionmaker(engine, expire_on_commit=False), engine


def _fake_trade(*, status: str = "Submitted") -> SimpleNamespace:
    """Trade object shape consumed by `_on_order_status`."""
    return SimpleNamespace(
        orderStatus=SimpleNamespace(status=status),
    )


def _fake_fill(*, exec_id: str, qty: int, price: float, side: str = "BOT",
               commission: float = 2.0) -> SimpleNamespace:
    return SimpleNamespace(
        execution=SimpleNamespace(
            execId=exec_id, shares=qty, price=price, side=side,
            time=datetime.now(UTC), exchange="CME",
        ),
        commissionReport=SimpleNamespace(commission=commission),
    )


async def _seed_structure(maker, *, qty: int = 5, n_legs: int = 1):
    from persistence.models import StructureOrder, TradeStructure
    async with maker() as db:
        s = TradeStructure(
            structure_type="straddle", reference_tenor="3M",
            base_qty=qty, state="submitted", execution_mode="live",
        )
        db.add(s)
        await db.flush()
        order_ids = []
        for i in range(n_legs):
            o = StructureOrder(
                structure_id=s.id, leg_idx=i, order_role="entry",
                contract_type="call" if i == 0 else "put",
                side="BUY", qty=qty, order_type="LMT",
                limit_price=1.234, preview_price=1.23, state="submitted",
            )
            db.add(o)
            await db.flush()
            order_ids.append(o.id)
        await db.commit()
        return s.id, order_ids


async def test_on_order_status_marks_rejected():
    from engines.execution.fills_handler import _on_order_status
    from persistence.models import StructureOrder

    maker, engine = await _make_session()
    try:
        _struct_id, order_ids = await _seed_structure(maker)
        await _on_order_status(_fake_trade(status="Inactive"), order_ids[0], maker)
        async with maker() as db:
            order = await db.get(StructureOrder, order_ids[0])
        assert order.state == "rejected"
        assert order.rejected_at is not None
    finally:
        await engine.dispose()


async def test_on_order_status_marks_cancelled():
    from engines.execution.fills_handler import _on_order_status
    from persistence.models import StructureOrder

    maker, engine = await _make_session()
    try:
        _struct_id, order_ids = await _seed_structure(maker)
        await _on_order_status(_fake_trade(status="Cancelled"), order_ids[0], maker)
        async with maker() as db:
            order = await db.get(StructureOrder, order_ids[0])
        assert order.state == "cancelled"
    finally:
        await engine.dispose()


async def test_on_execution_writes_fill_and_aggregates():
    from engines.execution.fills_handler import _on_execution
    from persistence.models import StructureFill, StructureOrder

    maker, engine = await _make_session()
    try:
        _struct_id, order_ids = await _seed_structure(maker, qty=5)
        oid = order_ids[0]
        # Two partial fills at different prices.
        await _on_execution(_fake_trade(), _fake_fill(
            exec_id="exec_1", qty=2, price=1.20, commission=1.0,
        ), oid, maker)
        await _on_execution(_fake_trade(), _fake_fill(
            exec_id="exec_2", qty=3, price=1.30, commission=1.5,
        ), oid, maker)
        async with maker() as db:
            from sqlalchemy import select
            fills = (await db.execute(
                select(StructureFill).where(StructureFill.order_id == oid)
            )).scalars().all()
            order = await db.get(StructureOrder, oid)
        assert len(fills) == 2
        assert order.qty_filled == 5
        # vwap = (2*1.20 + 3*1.30) / 5 = 1.26
        assert order.avg_fill_price == pytest.approx(1.26, abs=1e-6)
        assert order.total_commission_usd == pytest.approx(2.5)
        assert order.state == "filled"
        assert order.fully_filled_at is not None
    finally:
        await engine.dispose()


async def test_on_execution_idempotent_on_duplicate_exec_id():
    from engines.execution.fills_handler import _on_execution
    from persistence.models import StructureFill, StructureOrder

    maker, engine = await _make_session()
    try:
        _struct_id, order_ids = await _seed_structure(maker, qty=5)
        oid = order_ids[0]
        for _ in range(3):
            await _on_execution(_fake_trade(), _fake_fill(
                exec_id="exec_dup", qty=2, price=1.10,
            ), oid, maker)
        async with maker() as db:
            from sqlalchemy import select
            fills = (await db.execute(
                select(StructureFill).where(StructureFill.order_id == oid)
            )).scalars().all()
            order = await db.get(StructureOrder, oid)
        assert len(fills) == 1
        assert order.qty_filled == 2
        assert order.state == "partially_filled"
    finally:
        await engine.dispose()


async def test_full_fill_creates_position_and_marks_structure():
    """Two-leg structure : fill BOTH legs → trade_positions row should appear,
    structure flips to fully_filled, premium aggregated correctly."""
    from engines.execution.fills_handler import _on_execution
    from persistence.models import (
        TradePosition,
        TradeStructure,
    )

    maker, engine = await _make_session()
    try:
        struct_id, order_ids = await _seed_structure(maker, qty=2, n_legs=2)
        # Fill both legs in one go.
        await _on_execution(_fake_trade(), _fake_fill(
            exec_id="x1", qty=2, price=0.50, commission=1.0,
        ), order_ids[0], maker)
        await _on_execution(_fake_trade(), _fake_fill(
            exec_id="x2", qty=2, price=0.40, commission=1.0,
        ), order_ids[1], maker)

        async with maker() as db:
            from sqlalchemy import select
            struct = await db.get(TradeStructure, struct_id)
            pos = (await db.execute(
                select(TradePosition).where(TradePosition.structure_id == struct_id)
            )).scalar_one_or_none()
        assert struct.state == "fully_filled"
        assert struct.total_commission_usd == pytest.approx(2.0)
        # Both legs BUY × qty=2 × (0.50 + 0.40) = 1.80 signed total premium
        assert struct.total_premium_paid_usd == pytest.approx(1.80, abs=1e-4)
        assert pos is not None
        assert pos.state == "open"
    finally:
        await engine.dispose()
