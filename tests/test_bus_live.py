"""End-to-end integration tests for the Redis bus.

Runs the publisher helpers against a real Redis instance (the service
container the CI spins up, or a local ``docker compose`` redis). The
unit suite in ``tests/test_bus_publisher.py`` uses AsyncMock to prove
the contract ; this suite verifies that Redis actually honours it
(TTL expiration, pub/sub round-trip, multi-key pipeline semantics).

Gated by ``REDIS_RUN_INTEGRATION=1`` via conftest.py — skipped
otherwise. Locally :

    docker compose -f docker-compose.dev.yml up -d redis
    $env:REDIS_URL = "redis://localhost:6380/0"
    $env:REDIS_RUN_INTEGRATION = "1"
    python -m pytest tests/test_bus_live.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from redis import asyncio as aioredis

from bus import channels, keys
from bus.publisher import (
    publish_tick,
    publish_vol_update,
    reset_throttle_for_tests,
    set_heartbeat,
)

pytestmark = pytest.mark.redis_integration


@pytest.fixture
async def redis_client():
    """Fresh aioredis client per test, flushed through our deletion pattern.

    ``FLUSHDB`` is disabled by redis.conf (``rename-command FLUSHDB ""``)
    so we can't use it to wipe state between tests. Instead, each test
    deletes the specific keys it touches at the start and end — slower
    but works with the hardened config.
    """
    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("REDIS_URL not set")
    client = aioredis.from_url(url, decode_responses=True)
    yield client
    await client.aclose()


async def _delete_keys(client: aioredis.Redis, *keys_to_delete: str) -> None:
    """Best-effort delete (ignore missing)."""
    for k in keys_to_delete:
        await client.delete(k)


@pytest.mark.asyncio
async def test_pubsub_roundtrip(redis_client):
    """A published message reaches a subscriber on the same channel."""
    reset_throttle_for_tests()

    # Subscribe FIRST so we don't miss the publish. The subscriber task
    # runs concurrently with the publisher.
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channels.CH_TICKS)

    async def wait_for_message(timeout_s: float = 3.0) -> dict:
        deadline = asyncio.get_event_loop().time() + timeout_s
        while asyncio.get_event_loop().time() < deadline:
            msg = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=0.1
            )
            if msg is not None:
                return msg
        raise AssertionError("no message received within timeout")

    # Small sleep to ensure subscribe is registered server-side before publish.
    await asyncio.sleep(0.05)

    listener = asyncio.create_task(wait_for_message())
    await publish_tick(redis_client, "EURUSD", bid=1.0856, ask=1.0858, mid=1.0857)
    msg = await listener

    assert msg["type"] == "message"
    assert msg["channel"] == channels.CH_TICKS
    payload = json.loads(msg["data"])
    assert payload["symbol"] == "EURUSD"
    assert payload["mid"] == 1.0857

    await pubsub.unsubscribe(channels.CH_TICKS)
    await pubsub.aclose()
    await _delete_keys(
        redis_client,
        keys.LATEST_SPOT.format(symbol="EURUSD"),
        keys.LATEST_BID.format(symbol="EURUSD"),
        keys.LATEST_ASK.format(symbol="EURUSD"),
    )


@pytest.mark.asyncio
async def test_ttl_expires_spot_after_short_window(redis_client):
    """A key SET with a short TTL returns None once the TTL elapses.

    We use a 1-second TTL rather than the prescribed 30s to keep the
    test fast in CI. The mechanism under test (Redis honouring ``EX``)
    is identical at any TTL value ; shorter is just the same test in
    less wall-clock.
    """
    key = "smoke_ttl_test"
    await _delete_keys(redis_client, key)

    await redis_client.set(key, "hello", ex=1)
    assert await redis_client.get(key) == "hello"

    # TTL is in seconds on Redis ; pre-emptive check that Redis accepted it.
    ttl = await redis_client.ttl(key)
    assert 0 < ttl <= 1

    # Wait past the TTL and one scheduler tick for safety.
    await asyncio.sleep(1.5)
    assert await redis_client.get(key) is None


@pytest.mark.asyncio
async def test_pipeline_tick_to_vol_surface(redis_client):
    """A full tick-to-vol pipeline : MarketData pushes a tick, VolEngine
    pushes a surface, a downstream consumer MGETs both and finds them.
    """
    reset_throttle_for_tests()

    # Simulate MarketData push.
    await publish_tick(redis_client, "EURUSD", bid=1.0856, ask=1.0858, mid=1.0857)
    # Simulate VolEngine push (surface + signals).
    await publish_vol_update(
        redis_client,
        symbol="EURUSD",
        surface_data={
            "1M": {"dte": 30, "sigma_atm_pct": 7.5, "signal": "CHEAP"},
            "3M": {"dte": 90, "sigma_atm_pct": 8.0, "signal": "FAIR"},
        },
        signals_data=[{"tenor": "1M", "signal": "CHEAP", "ecart_pct": 0.1}],
    )
    # Simulate RiskEngine heartbeat.
    await set_heartbeat(redis_client, keys.ENGINE_RISK)

    spot, surface, signals, heartbeat = await redis_client.mget(
        keys.LATEST_SPOT.format(symbol="EURUSD"),
        keys.LATEST_VOL_SURFACE.format(symbol="EURUSD"),
        keys.LATEST_SIGNALS.format(symbol="EURUSD"),
        keys.HEARTBEAT.format(engine_name=keys.ENGINE_RISK),
    )

    assert spot == "1.0857"
    surface_payload = json.loads(surface)
    assert surface_payload["symbol"] == "EURUSD"
    assert set(surface_payload["surface"].keys()) == {"1M", "3M"}
    signals_payload = json.loads(signals)
    assert signals_payload["signals"][0]["tenor"] == "1M"
    assert heartbeat.endswith("Z")   # ISO-8601 UTC timestamp

    # Cleanup so a later run of the test does not see stale data from
    # a previous run's TTL window.
    await _delete_keys(
        redis_client,
        keys.LATEST_SPOT.format(symbol="EURUSD"),
        keys.LATEST_BID.format(symbol="EURUSD"),
        keys.LATEST_ASK.format(symbol="EURUSD"),
        keys.LATEST_VOL_SURFACE.format(symbol="EURUSD"),
        keys.LATEST_SIGNALS.format(symbol="EURUSD"),
        keys.HEARTBEAT.format(engine_name=keys.ENGINE_RISK),
    )


# Silence unused-import warnings for helpers available in the interactive
# session but not referenced by the tests above.
_ = (datetime, UTC, Decimal)
