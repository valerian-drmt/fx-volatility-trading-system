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

from tests.fixtures.db import make_sqlite_sessionmaker as _make_session  # shared harness (#338), FKs ON

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio


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
    assert await trade_mod._acquire_preview_lock(pid) == "acquired"
    assert await trade_mod._acquire_preview_lock(pid) == "held"  # NX collision
    assert fake.set_calls[0]["nx"] is True and fake.set_calls[0]["ex"] == 10


async def test_preview_lock_fails_closed_when_redis_unavailable(monkeypatch):
    """No Redis client = no lock = no submit (audit 04#2, #319). The old
    behaviour returned True here, which let a retry run the submit twice."""
    from api.routers import trade as trade_mod

    monkeypatch.setattr(trade_mod, "get_redis_client_or_none", lambda: None)
    assert await trade_mod._acquire_preview_lock("tp_abc") == "unavailable"


async def test_preview_lock_fails_closed_when_redis_raises(monkeypatch):
    from api.routers import trade as trade_mod

    monkeypatch.setattr(
        trade_mod, "get_redis_client_or_none", lambda: _FakeRedis(fail=True),
    )
    assert await trade_mod._acquire_preview_lock("tp_def") == "unavailable"


async def test_submit_refuses_without_lock_before_touching_the_preview(monkeypatch):
    """The handler maps 'unavailable' to a 503 and 'held' to a 409 BEFORE the
    preview is loaded, so a lock failure never reaches the IB gate or the DB."""
    from fastapi import HTTPException

    from api.routers import trade as trade_mod

    async def _unavailable(_pid, ttl_s=10):
        return "unavailable"

    async def _held(_pid, ttl_s=10):
        return "held"

    class _ExplodingDb:
        async def execute(self, *_a, **_k):
            raise AssertionError("preview must not be read without the lock")

    body = {"preview_id": "tp_locked", "execution_mode": "mock"}
    monkeypatch.setattr(trade_mod, "_acquire_preview_lock", _unavailable)
    with pytest.raises(HTTPException) as e1:
        await trade_mod._submit_preview_impl(body, _ExplodingDb())  # type: ignore[arg-type]
    assert e1.value.status_code == 503
    assert e1.value.detail["error"] == "submit_lock_unavailable"

    monkeypatch.setattr(trade_mod, "_acquire_preview_lock", _held)
    with pytest.raises(HTTPException) as e2:
        await trade_mod._submit_preview_impl(body, _ExplodingDb())  # type: ignore[arg-type]
    assert e2.value.status_code == 409


async def test_second_structure_for_one_preview_is_refused_by_the_schema():
    """UNIQUE trade_structure.preview_id (migration 053): the database, not the
    application, is the last guard against a double submit."""
    from sqlalchemy.exc import IntegrityError

    from persistence.models import TradePreviewRow, TradeStructure

    maker, engine = await _make_session()
    try:
        async with maker() as db:
            db.add(TradePreviewRow(
                preview_id="tp_once", expires_at=datetime.now(UTC),
                structure_type="straddle", reference_tenor="1M",
                structure_full_payload={}, pre_submit_checks=[],
                state="valid_for_submit",
            ))
            db.add(TradeStructure(
                preview_id="tp_once", structure_type="straddle",
                reference_tenor="1M", base_qty=1, state="submitted",
                execution_mode="mock",
            ))
            await db.commit()
        async with maker() as db:
            db.add(TradeStructure(
                preview_id="tp_once", structure_type="straddle",
                reference_tenor="1M", base_qty=1, state="submitted",
                execution_mode="mock",
            ))
            with pytest.raises(IntegrityError):
                await db.commit()
        # NULL preview_id (closing / hedge structures) stays unrestricted.
        async with maker() as db:
            for _ in range(2):
                db.add(TradeStructure(
                    preview_id=None, structure_type="straddle",
                    reference_tenor="1M", base_qty=1, state="submitted",
                    execution_mode="live",
                ))
            await db.commit()
    finally:
        await engine.dispose()


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
            assert row.product_label == "Strangle"
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
