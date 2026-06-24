"""Unit tests for the gating helpers added to api.routers.trade :
  * `_fetch_ib_connected` returns the cached IbConnectionState.is_connected
  * `_acquire_preview_lock` honours Redis NX semantics (and is best-effort
    when Redis is unreachable).

We don't test the full /trade/submit handler here (it pulls in the whole
preview-build pipeline + DB schema). Pytest hits the helpers directly.
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


async def test_fetch_ib_connected_reads_singleton_row():
    from api.routers.trade import _fetch_ib_connected
    from persistence.models import IbConnectionState

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            assert await _fetch_ib_connected(db) is False    # no row yet
            db.add(IbConnectionState(
                broker="IB", is_connected=True,
                last_heartbeat=datetime.now(UTC),
            ))
            await db.commit()
        async with maker() as db:
            assert await _fetch_ib_connected(db) is True
        async with maker() as db:
            row = (await db.execute(
                __import__("sqlalchemy").select(IbConnectionState)
            )).scalar_one()
            row.is_connected = False
            await db.commit()
        async with maker() as db:
            assert await _fetch_ib_connected(db) is False
    finally:
        await engine.dispose()


class _FakeRedis:
    """Mimic the subset of redis.asyncio used by _acquire_preview_lock."""

    def __init__(self, *, fail: bool = False):
        self._store: dict[str, bytes] = {}
        self.fail = fail
        self.set_calls: list[dict] = []

    async def set(self, key, value, ex=None, nx=False):
        self.set_calls.append({"key": key, "ex": ex, "nx": nx})
        if self.fail:
            raise RuntimeError("redis down")
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True


async def test_preview_lock_first_caller_wins(monkeypatch):
    from api.routers import trade as trade_mod

    fake = _FakeRedis()
    monkeypatch.setattr(trade_mod, "get_redis_client_or_none", lambda: fake)
    pid = "tp_xyz"
    assert await trade_mod._acquire_preview_lock(pid) is True
    assert await trade_mod._acquire_preview_lock(pid) is False  # NX collision


async def test_preview_lock_falls_back_when_redis_unavailable(monkeypatch):
    from api.routers import trade as trade_mod

    monkeypatch.setattr(trade_mod, "get_redis_client_or_none", lambda: None)
    assert await trade_mod._acquire_preview_lock("tp_abc") is True


async def test_preview_lock_falls_back_when_redis_raises(monkeypatch):
    from api.routers import trade as trade_mod

    monkeypatch.setattr(
        trade_mod, "get_redis_client_or_none", lambda: _FakeRedis(fail=True),
    )
    # Failure of Redis is non-fatal — DB user_action is the source of truth.
    assert await trade_mod._acquire_preview_lock("tp_def") is True


# ── G-trade.preview : free-legs path ──────────────────────────────────────


async def test_create_preview_from_free_legs(monkeypatch):
    """POST /trade/preview with `legs` builds a custom structure (no template),
    persists it, and labels it via the classifier — no Redis surface needed
    (falls back to the synthetic sandbox surface)."""
    from api.routers import trade as trade_mod
    from api.routers.trade import LegSpec, PreviewRequest, create_preview
    from persistence.models import TradePreviewRow

    # No Redis → _read_surface_redis returns (None, …) → synthetic surface.
    monkeypatch.setattr(trade_mod, "get_redis_client_or_none", lambda: None)

    maker, engine = await _make_session()
    try:
        req = PreviewRequest(legs=[
            LegSpec(contract_type="call", side="BUY", tenor="3M", delta_pillar="25dc"),
            LegSpec(contract_type="put", side="BUY", tenor="3M", delta_pillar="25dp"),
        ])
        async with maker() as db:
            payload = await create_preview(req, db, symbol="EURUSD")
        assert payload["structure"]["type"] == "long strangle"
        assert payload["structure"]["type_template"] == "custom"
        assert len(payload["structure"]["legs"]) == 2
        assert payload["state"] in ("valid_for_submit", "blocked")
        async with maker() as db:
            from sqlalchemy import select
            row = (await db.execute(select(TradePreviewRow))).scalar_one()
            assert row.structure_type == "long strangle"
            assert row.product_label == "long strangle"
    finally:
        await engine.dispose()


async def test_create_preview_requires_legs_or_structure_type():
    from fastapi import HTTPException

    from api.routers.trade import PreviewRequest, create_preview

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            with pytest.raises(HTTPException) as ei:
                await create_preview(PreviewRequest(), db, symbol="EURUSD")
            assert ei.value.status_code == 400
    finally:
        await engine.dispose()
