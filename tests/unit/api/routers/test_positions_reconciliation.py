"""GET /api/v1/positions/reconciliation — book (filled orders) vs broker (mirror).

Covers the pure diff classifier (``_compute_breaks``) and the end-to-end endpoint
(a quantity break where IB under-holds + an unbooked orphan IB holds).
"""
from __future__ import annotations

from datetime import UTC, datetime

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


def _pos(**kw):
    from persistence.models import OpenPosition
    base = dict(side="BUY", quantity=10, entry_timestamp=datetime.now(UTC))
    base.update(kw)
    return OpenPosition(**base)


async def test_compute_breaks_classifies_each_kind():
    """Pure diff logic (no DB) ; async only to match the module's asyncio mark."""
    from api.routers.positions import _compute_breaks

    expected = {"OK": 5.0, "MISS": 3.0, "QTY": 10.0, "DIR": 4.0, "NOISE": 1.00004}
    actual = {"OK": 5.0, "QTY": 6.0, "DIR": -4.0, "NOISE": 1.0, "ORPHAN": 2.0}
    struct = {"OK": 1, "MISS": 2, "QTY": 3, "DIR": 4, "ORPHAN": None}
    by = {b["contract"]: b for b in _compute_breaks(expected, actual, struct)}

    assert "OK" not in by and "NOISE" not in by
    assert by["MISS"]["kind"] == "missing_at_ib" and by["MISS"]["structure_id"] == 2
    assert by["QTY"]["kind"] == "quantity" and by["QTY"]["break"] == 4.0
    assert by["DIR"]["kind"] == "direction"
    assert by["ORPHAN"]["kind"] == "unbooked_at_ib" and by["ORPHAN"]["structure_id"] is None


async def test_reconciliation_endpoint_flags_a_quantity_break():
    """Filled orders (book) vs IB mirror : a leg IB under-holds is a break ; a
    matched leg is clean ; an IB row with no order is unbooked."""
    from api.routers.positions import reconciliation
    from persistence.models import StructureOrder, TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            s = TradeStructure(structure_type="strangle", reference_tenor="3M",
                               base_qty=10, state="partial_fill")
            db.add(s)
            await db.flush()
            db.add_all([
                StructureOrder(structure_id=s.id, leg_idx=0, contract_type="call", side="BUY",
                               qty=10, qty_filled=10, state="filled", ib_local_symbol="EUUV6 C1130"),
                StructureOrder(structure_id=s.id, leg_idx=1, contract_type="put", side="BUY",
                               qty=10, qty_filled=10, state="filled", ib_local_symbol="EUUV6 P1090"),
            ])
            db.add_all([
                _pos(structure="EUUV6 C1130", side="BUY", quantity=10, trade_id=s.id),
                _pos(structure="EUUV6 P1090", side="BUY", quantity=6, trade_id=s.id),
                _pos(structure="EUUU6 C1200", side="SELL", quantity=3, trade_id=None),  # orphan
            ])
            await db.commit()
            out = await reconciliation(db)

        by = {b["contract"]: b for b in out["breaks"]}
        assert "EUUV6 C1130" not in by                 # matched → no break
        assert by["EUUV6 P1090"]["kind"] == "quantity"
        assert by["EUUV6 P1090"]["break"] == 4.0       # +10 book − (+6) IB
        assert by["EUUV6 P1090"]["structure_id"] == s.id
        assert by["EUUU6 C1200"]["kind"] == "unbooked_at_ib"
        assert out["n_breaks"] == 2
    finally:
        await engine.dispose()
