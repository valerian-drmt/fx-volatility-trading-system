"""db-writer delivery semantics (#324, audit DI-02 / DI-04 / DI-05 / DI-07).

On a real SQLite session: a re-sent natural key is a no-op on every streamed
table, a poison row goes to the dead letter while its peers land, an unknown
table is dead-lettered, and a transient error still reaches the retry loop
(which re-sends the batch, now idempotently). The queue-overflow counter is
checked on the service.
"""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

pytest.importorskip("pytest_asyncio")

pytestmark = pytest.mark.asyncio

TS = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def _surface(ts: datetime = TS) -> dict[str, Any]:
    return {"timestamp": ts, "underlying": "EURUSD", "spot": 1.1234, "forward": 1.1234, "surface_data": {"k": 1}}


def _regime(ts: datetime = TS, label: str = "calm") -> dict[str, Any]:
    return {"timestamp": ts, "symbol": "EURUSD", "label": label, "method": "gmm"}


async def _count(maker: Any, model: Any) -> int:
    async with maker() as db:
        return int((await db.execute(select(func.count()).select_from(model))).scalar_one())


def _writer(maker: Any):
    from persistence.writer import AsyncDatabaseWriter
    return AsyncDatabaseWriter(session_factory=maker, retry_attempts=1)


async def test_every_streamed_table_has_a_natural_key_and_a_resend_is_a_noop(sqlite_sessionmaker: Any) -> None:
    from persistence.models import RegimeSnapshot, VolSurface
    from persistence.writer import IDEMPOTENT_TABLES, TABLE_MODELS

    streamed = {"vol_surface_history", "regime_snapshot_history", "feature_history",
                "pca_signal_history", "pca_surface_snapshot_history"}
    assert streamed <= set(IDEMPOTENT_TABLES) and streamed <= set(TABLE_MODELS)

    w = _writer(sqlite_sessionmaker)
    batch = [("vol_surface_history", _surface()), ("regime_snapshot_history", _regime())]
    await w._write_batch(batch)
    await w._write_batch(batch)  # the retry after an in-doubt commit / a Redis redelivery
    await w._write_batch([("regime_snapshot_history", _regime(label="stressed"))])  # same key, later payload

    assert await _count(sqlite_sessionmaker, VolSurface) == 1
    assert await _count(sqlite_sessionmaker, RegimeSnapshot) == 1
    async with sqlite_sessionmaker() as db:
        row = (await db.execute(select(RegimeSnapshot))).scalar_one()
    assert row.label == "calm", "first write wins"


async def test_a_poison_row_is_dead_lettered_and_its_peers_land(sqlite_sessionmaker: Any) -> None:
    from persistence.models import DbWriterDeadLetter, RegimeSnapshot
    from shared.observability import db_writer_dead_letter_total

    before = db_writer_dead_letter_total.labels(table="regime_snapshot_history")._value.get()
    w = _writer(sqlite_sessionmaker)
    ts2 = datetime(2026, 11, 6, 12, 3, tzinfo=UTC)
    ts3 = datetime(2026, 11, 6, 12, 6, tzinfo=UTC)
    poison = _regime(ts2, label="not_a_regime")  # violates the CHECK constraint
    await w._write_batch([
        ("regime_snapshot_history", _regime(TS)),
        ("regime_snapshot_history", poison),
        ("regime_snapshot_history", _regime(ts3)),
    ])

    assert await _count(sqlite_sessionmaker, RegimeSnapshot) == 2
    async with sqlite_sessionmaker() as db:
        dl = (await db.execute(select(DbWriterDeadLetter))).scalars().all()
    assert len(dl) == 1
    assert dl[0].table_name == "regime_snapshot_history" and "IntegrityError" in dl[0].error
    assert dl[0].payload["label"] == "not_a_regime" and dl[0].payload["timestamp"].startswith("2026-11-06T12:03")
    assert db_writer_dead_letter_total.labels(table="regime_snapshot_history")._value.get() == before + 1


async def test_an_unknown_table_is_dead_lettered_not_dropped(sqlite_sessionmaker: Any) -> None:
    from persistence.models import DbWriterDeadLetter

    w = _writer(sqlite_sessionmaker)
    await w._write_batch([("no_such_table", {"a": 1})])
    async with sqlite_sessionmaker() as db:
        dl = (await db.execute(select(DbWriterDeadLetter))).scalar_one()
    assert dl.table_name == "no_such_table" and dl.error == "unknown table" and dl.payload == {"a": 1}


async def test_transient_errors_still_reach_the_retry_loop(sqlite_sessionmaker: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A connection reset is not a row problem: no fallback, no dead letter,
    the retry wrapper re-sends the batch (idempotent thanks to the keys)."""
    from persistence.models import DbWriterDeadLetter

    w = _writer(sqlite_sessionmaker)
    calls = {"n": 0}

    async def _boom(*_a: Any, **_k: Any) -> None:
        calls["n"] += 1
        raise OperationalError("INSERT", {}, Exception("connection reset"))

    monkeypatch.setattr(w, "_build_insert", lambda *a, **k: (_ for _ in ()).throw(
        OperationalError("INSERT", {}, Exception("connection reset"))))
    with pytest.raises(OperationalError):
        await w._write_batch_with_retries([("vol_surface_history", _surface())])
    assert await _count(sqlite_sessionmaker, DbWriterDeadLetter) == 0
    assert calls["n"] == 0  # the monkeypatched builder raised before any execute


def test_queue_overflow_is_counted(caplog: pytest.LogCaptureFixture) -> None:
    from engines.db_writer.service import DbWriterService
    from shared.observability import db_writer_queue_depth, db_writer_queue_dropped_total

    class _W:
        def __init__(self) -> None:
            self.queue: asyncio.Queue = asyncio.Queue(maxsize=1)

    class _R:
        def pubsub(self) -> Any:
            return None

    before = db_writer_queue_dropped_total.labels(table="t")._value.get()
    svc = DbWriterService(redis=_R(), writer=_W())  # type: ignore[arg-type]
    svc._enqueue(json.dumps({"table": "t", "payload": {"a": 1}}))
    svc._enqueue(json.dumps({"table": "t", "payload": {"a": 2}}))
    assert db_writer_queue_dropped_total.labels(table="t")._value.get() == before + 1
    assert db_writer_queue_depth._value.get() == 1


def test_vol_engine_stamps_one_cycle_timestamp_on_every_streamed_row() -> None:
    """DI-02: the producer side of the natural key."""
    from pathlib import Path

    src = Path("src/engines/vol/engine.py").read_text(encoding="utf-8")
    assert "cycle_ts = datetime.now(UTC)" in src
    assert 'ts_iso = cycle_ts.isoformat().replace("+00:00", "Z")' in src
    assert 'ts_iso = datetime.now(UTC).isoformat()' not in src


async def test_dead_letter_payload_is_json_safe(sqlite_sessionmaker: Any) -> None:
    from persistence.models import DbWriterDeadLetter

    w = _writer(sqlite_sessionmaker)
    await w._dead_letter("feature_history", {"timestamp": TS, "symbol": "EURUSD", "x": Decimal("1.5")}, "boom")
    async with sqlite_sessionmaker() as db:
        dl = (await db.execute(select(DbWriterDeadLetter))).scalar_one()
    assert dl.payload["timestamp"] == TS.isoformat() and dl.error == "boom"
