"""Unit tests for GET /api/v1/bars — the handler reads a JSON list from the
market-data engine's Redis cache and returns the last ``limit`` BarRows."""
from __future__ import annotations

import json

import pytest

from api.routers.analytics import bars

pytestmark = pytest.mark.asyncio

_ROWS = [
    {"t": 1_704_067_200_000, "o": 1.09, "h": 1.10, "l": 1.08, "c": 1.10},
    {"t": 1_704_070_800_000, "o": 1.10, "h": 1.11, "l": 1.09, "c": 1.105},
    {"t": 1_704_074_400_000, "o": 1.105, "h": 1.12, "l": 1.10, "c": 1.118},
]


class _FakeRedis:
    def __init__(self, value: str | None) -> None:
        self._value = value
        self.asked: str | None = None

    async def get(self, key: str) -> str | None:
        self.asked = key
        return self._value


async def test_bars_returns_last_limit_rows():
    redis = _FakeRedis(json.dumps(_ROWS))
    out = await bars(redis, symbol="EURUSD", tf="1W", limit=2)
    assert [r.t for r in out] == [1_704_070_800_000, 1_704_074_400_000]
    assert out[-1].c == 1.118
    assert redis.asked == "bars:EURUSD:1W"


async def test_bars_empty_when_cache_missing():
    assert await bars(_FakeRedis(None), symbol="EURUSD", tf="1D", limit=48) == []


async def test_bars_unknown_timeframe_is_empty():
    redis = _FakeRedis(json.dumps(_ROWS))
    assert await bars(redis, symbol="EURUSD", tf="5m", limit=48) == []
    assert redis.asked is None  # never hit Redis for an unknown tf
