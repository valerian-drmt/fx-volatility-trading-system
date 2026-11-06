"""In-memory SQLite session factory for unit tests (#338, audit 07#11 / T7-03).

One implementation replaces the fourteen copies of ``_make_session`` that each
test module carried. Two things the copies got wrong, fixed here once:

- ``PRAGMA foreign_keys = ON`` on every connection. SQLite ignores foreign
  keys by default, so a test could insert a fill for a missing order or a
  hedge for a missing position and pass, while Postgres would refuse it.
  ``foreign_keys=False`` is an explicit, visible opt-out for the few tests
  that build rows in isolation on purpose.
- ``BigInteger`` → ``Integer`` coercion done once, idempotently (SQLite only
  autoincrements plain INTEGER primary keys).
"""
from __future__ import annotations

from typing import Any

_COERCED = False


def coerce_bigint_to_integer(metadata: Any) -> None:
    """SQLite autoincrements only INTEGER PRIMARY KEY; the models use BigInteger."""
    global _COERCED
    if _COERCED:
        return
    from sqlalchemy import BigInteger, Integer

    for table in metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, BigInteger):
                col.type = Integer()
    _COERCED = True


async def make_sqlite_sessionmaker(*, foreign_keys: bool = True) -> tuple[Any, Any]:
    """``(async_sessionmaker, engine)`` on a fresh in-memory DB with the full
    schema. Dispose the engine when done (``await engine.dispose()``)."""
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from persistence.models import Base

    coerce_bigint_to_integer(Base.metadata)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    if foreign_keys:
        @event.listens_for(engine.sync_engine, "connect")
        def _fk_on(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine
