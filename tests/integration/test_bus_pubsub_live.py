"""redis_integration test for the Redis bus pub/sub path.

A live round-trip through ``bus.client.get_redis()``: subscribe to a bus channel,
PUBLISH on it, and assert the subscriber receives the payload. Exercises the real
async client factory + Redis connectivity that the engines and the WS bridge rely
on.

Gated by the ``redis_integration`` marker — needs a live Redis with ``REDIS_URL``
set and ``REDIS_RUN_INTEGRATION=1``.

The client is created via an ``@asynccontextmanager`` (not a pytest async fixture)
because the pinned pytest-asyncio (0.23.x) can't wrap async fixtures on pytest 9.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import pytest

pytestmark = [pytest.mark.redis_integration, pytest.mark.asyncio]


@asynccontextmanager
async def _live_redis():
    if not os.environ.get("REDIS_URL") or not os.environ.get("REDIS_RUN_INTEGRATION"):
        pytest.skip("redis_integration: set REDIS_RUN_INTEGRATION=1 and REDIS_URL")
    from bus.client import get_redis, reset_clients_for_tests

    reset_clients_for_tests()
    client = get_redis()
    try:
        yield client
    finally:
        await client.aclose()
        reset_clients_for_tests()


async def test_bus_pubsub_round_trip():
    from bus import channels

    async with _live_redis() as client:
        pubsub = client.pubsub()
        await pubsub.subscribe(channels.CH_SYSTEM_ALERTS)
        await pubsub.get_message(timeout=1.0)  # drain the subscribe confirmation

        delivered = await client.publish(channels.CH_SYSTEM_ALERTS, "ping")
        assert delivered >= 1  # our own subscriber received it

        received = None
        for _ in range(20):
            msg = await pubsub.get_message(timeout=0.5)
            if msg and msg.get("type") == "message":
                received = msg
                break

        await pubsub.unsubscribe(channels.CH_SYSTEM_ALERTS)
        await pubsub.aclose()

    assert received is not None
    assert received["data"] == "ping"  # decode_responses=True → str, not bytes
