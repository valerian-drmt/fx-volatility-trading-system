"""Backoff math + connect-retry behaviour of ``shared.ib_connection``."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.ib_connection import (
    MAX_BACKOFF_S,
    MIN_BACKOFF_S,
    connect_ib_with_backoff,
    next_backoff_seconds,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    "attempt,expected",
    [
        (0, 1.0),
        (1, 2.0),
        (2, 4.0),
        (3, 8.0),
        (4, 16.0),
        (5, 32.0),
    ],
)
def test_backoff_doubles_each_attempt(attempt: int, expected: float):
    assert next_backoff_seconds(attempt) == expected


@pytest.mark.unit
def test_backoff_caps_at_60s():
    assert next_backoff_seconds(6) == MAX_BACKOFF_S
    assert next_backoff_seconds(20) == MAX_BACKOFF_S
    assert next_backoff_seconds(100) == MAX_BACKOFF_S


@pytest.mark.unit
def test_backoff_clamps_negative_attempts_to_min():
    assert next_backoff_seconds(-1) == MIN_BACKOFF_S
    assert next_backoff_seconds(-5) == MIN_BACKOFF_S


@pytest.mark.asyncio
async def test_connect_returns_immediately_when_already_connected():
    ib = MagicMock()
    ib.isConnected = MagicMock(return_value=True)
    ib.connectAsync = AsyncMock()

    await connect_ib_with_backoff(ib, host="ib-gateway", port=4002, client_id=1)

    ib.connectAsync.assert_not_called()


@pytest.mark.asyncio
async def test_connect_succeeds_on_second_attempt(monkeypatch):
    # First connect raises ConnectionRefused, second succeeds.
    ib = MagicMock()
    connection_state = {"connected": False}

    def is_connected() -> bool:
        return connection_state["connected"]

    async def connect_async(*_args: Any, **_kwargs: Any) -> None:
        if not hasattr(connect_async, "_called_once"):
            connect_async._called_once = True
            raise ConnectionRefusedError("IB gateway not ready")
        connection_state["connected"] = True

    ib.isConnected = is_connected
    ib.connectAsync = AsyncMock(side_effect=connect_async)

    # Monkeypatch asyncio.sleep to a no-op so the test doesn't wait 1 s.
    import asyncio

    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    await connect_ib_with_backoff(ib, host="ib-gateway", port=4002, client_id=1)
    assert connection_state["connected"]


@pytest.mark.asyncio
async def test_connect_raises_after_max_attempts(monkeypatch):
    ib = MagicMock()
    ib.isConnected = MagicMock(return_value=False)
    ib.connectAsync = AsyncMock(side_effect=ConnectionRefusedError("down"))

    import asyncio

    async def fast_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)

    with pytest.raises(ConnectionError, match="unreachable after 3 attempts"):
        await connect_ib_with_backoff(
            ib, host="ib-gateway", port=4002, client_id=1, max_attempts=3
        )
    assert ib.connectAsync.call_count == 3
