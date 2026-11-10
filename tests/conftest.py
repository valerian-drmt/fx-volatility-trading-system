"""Root pytest fixtures (#338, audit 07).

- ``sqlite_sessionmaker``: in-memory SQLite with the full schema and FOREIGN
  KEYS ON (one implementation for the whole suite, see ``tests/fixtures/db.py``).
- ``frozen_clock``: freezegun at a fixed instant; returns the frozen datetime.
- ``ib_factories``: the ib_insync object factories (trade / fill / report / IB).
- ``canonical_payload``: loader for the one-per-channel / per-event payloads.

Test order is randomised by pytest-randomly (dev extra); the seed is printed
at the top of the run and can be replayed with ``-p randomly --randomly-seed=N``.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from tests.fixtures import ib as ib_fixtures
from tests.fixtures.db import make_sqlite_sessionmaker
from tests.fixtures.payloads import payload

FROZEN_AT = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
async def sqlite_sessionmaker() -> AsyncIterator[Any]:
    maker, engine = await make_sqlite_sessionmaker(foreign_keys=True)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
async def sqlite_sessionmaker_no_fk() -> AsyncIterator[Any]:
    """Explicit opt-out for tests that build rows in isolation on purpose."""
    maker, engine = await make_sqlite_sessionmaker(foreign_keys=False)
    try:
        yield maker
    finally:
        await engine.dispose()


@pytest.fixture
def frozen_clock() -> Iterator[datetime]:
    from freezegun import freeze_time

    with freeze_time(FROZEN_AT):
        yield FROZEN_AT


@pytest.fixture
def ib_factories() -> Any:
    return ib_fixtures


@pytest.fixture
def canonical_payload() -> Callable[[str], dict[str, Any]]:
    return payload


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    """slowapi's limiter is a module singleton keyed on the client address, and
    every TestClient is "testclient": without a reset, login calls across the
    whole session share one 5/minute bucket (#296)."""
    try:
        from api.auth import lockout
        from api.middleware.rate_limit import limiter
    except Exception:  # api extras not installed in this environment
        yield
        return
    limiter.reset()
    lockout.reset_all()
    yield
    limiter.reset()
    lockout.reset_all()
