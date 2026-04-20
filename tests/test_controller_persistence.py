"""Tests for R2 PR #3 — persistence settings + DbWriterThread wiring.

Three layers of coverage :

1. Settings validation (pure static methods, no Controller instance):
   legacy settings files without a persistence section get the defaults
   added on load, and invalid values are normalized.

2. DbWriterThread lifecycle against a file-backed aiosqlite DB :
   start, ready, enqueue, stop, rows land in the DB.

3. Controller.enqueue_db_event no-op when the writer thread is absent
   (persistence disabled OR failed to start).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from controller import Controller
from persistence.models import AccountSnap, Base
from persistence.writer_thread import DbWriterThread

# --- 1. Settings validation --------------------------------------------------


@pytest.mark.unit
class TestValidatePersistenceSettings:
    def test_defaults_applied_when_missing(self):
        r = Controller._validate_persistence_settings({})
        assert r == {"enabled": True, "database_url": None}

    def test_enabled_is_coerced_to_bool(self):
        r = Controller._validate_persistence_settings({"enabled": 0})
        assert r["enabled"] is False

    def test_database_url_whitespace_is_normalized(self):
        r = Controller._validate_persistence_settings(
            {"database_url": "  postgresql://x  "}
        )
        assert r["database_url"] == "postgresql://x"

    def test_database_url_empty_string_becomes_none(self):
        r = Controller._validate_persistence_settings({"database_url": "   "})
        assert r["database_url"] is None

    def test_rejects_non_dict(self):
        with pytest.raises(ValueError, match="JSON object"):
            Controller._validate_persistence_settings("enabled=true")


@pytest.mark.unit
class TestValidateAppSettingsWithPersistence:
    def test_legacy_payload_gets_persistence_defaults(self):
        r = Controller._validate_app_settings({
            "status": {"host": "127.0.0.1", "port": 4002, "market_symbol": "EURUSD"},
            "runtime": {"tick_interval_ms": 100, "snapshot_interval_ms": 2000},
        })
        assert r["persistence"] == {"enabled": True, "database_url": None}

    def test_persistence_section_preserved(self):
        r = Controller._validate_app_settings({
            "status": {"host": "127.0.0.1", "port": 4002, "market_symbol": "EURUSD"},
            "persistence": {"enabled": False, "database_url": "postgresql://x"},
        })
        assert r["persistence"]["enabled"] is False
        assert r["persistence"]["database_url"] == "postgresql://x"


# --- 2. DbWriterThread lifecycle --------------------------------------------


@pytest.fixture
def sqlite_db_url(tmp_path):
    """Create a file-backed aiosqlite DB with the schema, return the URL.

    The DbWriterThread creates its own engine from this URL on its own
    asyncio loop, so we can't share an engine — we pre-create the schema
    here, close our engine, and hand the URL over.
    """
    db_path = tmp_path / "writer_test.sqlite"
    url = f"sqlite+aiosqlite:///{db_path}"

    async def _setup() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(_setup())
    return url


@pytest.mark.unit
def test_db_writer_thread_start_and_stop_without_enqueue(sqlite_db_url):
    """Full start/stop cycle with no events — must not hang, must not error."""
    t = DbWriterThread(database_url=sqlite_db_url, batch_timeout_s=0.3)
    t.start()
    assert t.wait_until_ready(timeout=2.0)
    t.stop(timeout=3.0)
    assert not t.is_alive()


@pytest.mark.unit
def test_db_writer_thread_enqueue_reaches_db(sqlite_db_url):
    """Events enqueued from the test thread land in the DB after shutdown."""
    t = DbWriterThread(database_url=sqlite_db_url, batch_timeout_s=0.3)
    t.start()
    assert t.wait_until_ready(timeout=2.0)

    for i in range(5):
        t.enqueue(
            "account_snaps",
            {"timestamp": datetime(2026, 4, 20, 10, 0, i, tzinfo=UTC)},
        )

    t.stop(timeout=3.0)
    assert not t.is_alive()

    async def _count() -> int:
        engine = create_async_engine(sqlite_db_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            n = await session.scalar(select(func.count()).select_from(AccountSnap))
        await engine.dispose()
        return n

    assert asyncio.run(_count()) == 5


@pytest.mark.unit
def test_db_writer_thread_enqueue_before_start_is_safe(sqlite_db_url):
    """Calling enqueue before start() must not raise — it logs and drops."""
    t = DbWriterThread(database_url=sqlite_db_url)
    # Should be a no-op (writer not created yet).
    t.enqueue("account_snaps", {"timestamp": datetime.now(UTC)})
    # Thread is not started, never will be in this test.
    assert not t.is_alive()


@pytest.mark.unit
def test_db_writer_thread_stop_is_idempotent(sqlite_db_url):
    """A second stop() after the thread is dead must not raise."""
    t = DbWriterThread(database_url=sqlite_db_url, batch_timeout_s=0.3)
    t.start()
    assert t.wait_until_ready(timeout=2.0)
    t.stop(timeout=3.0)
    t.stop(timeout=1.0)  # idempotent


# --- 3. Controller.enqueue_db_event no-op path ------------------------------


@pytest.mark.unit
class TestControllerEnqueueDbEvent:
    def test_no_op_when_writer_thread_is_none(self):
        """Most common case : persistence disabled or startup failed."""
        c = Controller.__new__(Controller)
        c._db_writer_thread = None
        # Must not raise even with a perfectly fine payload.
        c.enqueue_db_event("account_snaps", {"timestamp": datetime.now(UTC)})

    def test_delegates_to_thread_enqueue(self):
        """If a writer thread is attached, the call is forwarded verbatim."""
        calls: list[tuple[str, dict]] = []

        class FakeThread:
            def enqueue(self, table_name, payload):
                calls.append((table_name, payload))

        c = Controller.__new__(Controller)
        c._db_writer_thread = FakeThread()
        c.enqueue_db_event("vol_surfaces", {"k": "v"})
        assert calls == [("vol_surfaces", {"k": "v"})]
