"""Tests for VolEngine._read_spot — tolerance to both wire formats.

The Redis publisher writes ``str(mid)`` (a plain float string) whereas
older code paths wrote a JSON dict ``{"mid": x, "bid": y}``. The engine
must now accept both.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("pytest_asyncio")


def _engine_with_redis(redis_mock):
    from engines.vol.engine import VolEngine

    return VolEngine(
        ib=MagicMock(),
        redis=redis_mock,
        symbol="EURUSD",
        ib_host="ib-gateway",
        ib_port=4002,
        client_id=1,
        fetch_fop_chain=lambda _F: {},
        fetch_ohlc=lambda: None,
    )


@pytest.mark.asyncio
async def test_read_spot_accepts_plain_float_string() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"1.17352")
    engine = _engine_with_redis(redis)
    assert await engine._read_spot() == pytest.approx(1.17352)


@pytest.mark.asyncio
async def test_read_spot_accepts_plain_str_without_bytes() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value="1.10")
    engine = _engine_with_redis(redis)
    assert await engine._read_spot() == pytest.approx(1.10)


@pytest.mark.asyncio
async def test_read_spot_accepts_legacy_json_dict() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value='{"mid": 1.08, "bid": 1.079}')
    engine = _engine_with_redis(redis)
    assert await engine._read_spot() == pytest.approx(1.08)


@pytest.mark.asyncio
async def test_read_spot_returns_none_when_missing() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    engine = _engine_with_redis(redis)
    assert await engine._read_spot() is None


@pytest.mark.asyncio
async def test_read_spot_returns_none_on_garbage() -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"not-a-number")
    engine = _engine_with_redis(redis)
    assert await engine._read_spot() is None
