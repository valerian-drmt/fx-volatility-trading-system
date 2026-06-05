"""Unit tests for bus.publisher — R3 PR #3.

Every test uses an ``AsyncMock`` redis client to capture SET and PUBLISH
calls. No real Redis, no network : the contract we verify is
"the right SET and PUBLISH were issued with the right TTLs".

Live Postgres + Redis coverage lands in R3 PR #6 (test_bus_live.py).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, call

import pytest

from bus import channels, keys
from bus.publisher import (
    publish_account,
    publish_risk_update,
    publish_tick,
    publish_vol_update,
    reset_throttle_for_tests,
    set_heartbeat,
)


@pytest.fixture(autouse=True)
def _reset_throttle():
    """Ensure no throttle state bleeds between tests."""
    reset_throttle_for_tests()
    yield
    reset_throttle_for_tests()


@pytest.fixture
def redis_mock() -> AsyncMock:
    client = AsyncMock()
    client.set = AsyncMock(return_value=True)
    client.publish = AsyncMock(return_value=1)
    return client


# --- publish_tick -----------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_tick_sets_latest_spot_and_publishes(redis_mock):
    """First tick writes the 3 cache keys with TTL 30s AND publishes once."""
    emitted = await publish_tick(
        redis_mock, symbol="EURUSD", bid=1.0856, ask=1.0858, mid=1.0857
    )

    assert emitted is True
    redis_mock.set.assert_any_call("latest_spot:EURUSD", "1.0857", ex=keys.TTL_SPOT)
    redis_mock.set.assert_any_call("latest_bid:EURUSD", "1.0856", ex=keys.TTL_BID_ASK)
    redis_mock.set.assert_any_call("latest_ask:EURUSD", "1.0858", ex=keys.TTL_BID_ASK)
    assert redis_mock.publish.call_count == 1

    channel, payload = redis_mock.publish.call_args[0]
    assert channel == channels.CH_TICKS
    decoded = json.loads(payload)
    assert decoded == {
        "symbol": "EURUSD",
        "bid": 1.0856,
        "ask": 1.0858,
        "mid": 1.0857,
        "timestamp": decoded["timestamp"],  # exact value varies
    }
    assert decoded["timestamp"].endswith("Z")


@pytest.mark.asyncio
async def test_tick_throttle_buckets_200ms(redis_mock):
    """50 ticks pushed within ~10ms must emit at most 1 PUBLISH."""
    for i in range(50):
        await publish_tick(redis_mock, "EURUSD", 1.0 + i * 1e-6, 1.0 + i * 1e-6, 1.0)

    # All 50 ticks update the cache (3 SETs each = 150 SETs total).
    assert redis_mock.set.call_count == 150
    # Throttle keeps PUBLISH count at 1 (first call won, others within 200ms).
    assert redis_mock.publish.call_count == 1


@pytest.mark.asyncio
async def test_tick_throttle_is_per_symbol(redis_mock):
    """One burst on EURUSD and one on GBPUSD must both publish (different buckets)."""
    await publish_tick(redis_mock, "EURUSD", 1.08, 1.09, 1.085)
    await publish_tick(redis_mock, "GBPUSD", 1.26, 1.27, 1.265)
    # Second call same symbol : throttled.
    await publish_tick(redis_mock, "EURUSD", 1.08, 1.09, 1.085)

    assert redis_mock.publish.call_count == 2


# --- publish_account -------------------------------------------------------


@pytest.mark.asyncio
async def test_publish_account_sets_snapshot_and_publishes(redis_mock):
    payload = {"net_liq_usd": 125_000, "open_positions_count": 4}
    await publish_account(redis_mock, payload)

    redis_mock.set.assert_called_once()
    (key_, val_), kw = redis_mock.set.call_args
    assert key_ == keys.ACCOUNT_SNAPSHOT
    assert kw == {"ex": keys.TTL_ACCOUNT}
    body = json.loads(val_)
    assert body["net_liq_usd"] == 125_000
    assert "timestamp" in body

    redis_mock.publish.assert_called_once()
    assert redis_mock.publish.call_args[0][0] == channels.CH_ACCOUNT


# --- publish_vol_update ----------------------------------------------------


@pytest.mark.asyncio
async def test_publish_vol_update_sets_surface_and_signals_and_publishes(redis_mock):
    """One call must issue 2 SETs (surface + signals) and 1 PUBLISH."""
    await publish_vol_update(
        redis_mock,
        symbol="EURUSD",
        surface_data={"1M": {"atm": 7.5}},
        signals_data=[{"tenor": "1M", "type": "CHEAP"}],
    )

    assert redis_mock.set.call_count == 2
    keys_written = [args[0] for args, _ in redis_mock.set.call_args_list]
    assert "latest_vol_surface:EURUSD" in keys_written
    assert "latest_signals:EURUSD" in keys_written

    ttl_written = {
        args[0]: kw["ex"] for args, kw in redis_mock.set.call_args_list
    }
    assert ttl_written["latest_vol_surface:EURUSD"] == keys.TTL_VOL_SURFACE
    assert ttl_written["latest_signals:EURUSD"] == keys.TTL_SIGNALS

    redis_mock.publish.assert_called_once()
    assert redis_mock.publish.call_args[0][0] == channels.CH_VOL_UPDATE


# --- publish_risk_update ---------------------------------------------------


@pytest.mark.asyncio
async def test_publish_risk_update_with_and_without_pnl(redis_mock):
    greeks = {"delta_net": 1200, "vega_net": 500}

    await publish_risk_update(redis_mock, greeks, pnl_curve=None)
    # 1 SET (greeks) + 1 PUBLISH (risk_update), no pnl SET
    assert redis_mock.set.call_count == 1
    assert redis_mock.publish.call_count == 1
    assert redis_mock.set.call_args[0][0] == keys.LATEST_GREEKS_PORTFOLIO

    redis_mock.set.reset_mock()
    redis_mock.publish.reset_mock()

    await publish_risk_update(
        redis_mock, greeks, pnl_curve={"spots": [1, 2], "pnls": [0, 10]}
    )
    # 2 SET (greeks + pnl curve) + 1 PUBLISH
    assert redis_mock.set.call_count == 2
    assert redis_mock.publish.call_count == 1
    keys_set = [args[0] for args, _ in redis_mock.set.call_args_list]
    assert keys.LATEST_GREEKS_PORTFOLIO in keys_set
    assert keys.LATEST_PNL_CURVE in keys_set


# --- set_heartbeat ---------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat_uses_30s_ttl(redis_mock):
    await set_heartbeat(redis_mock, keys.ENGINE_VOL)

    assert redis_mock.set.call_count == 1
    (key_, val_), kw = redis_mock.set.call_args
    assert key_ == "heartbeat:vol_engine"
    assert kw == {"ex": keys.TTL_HEARTBEAT}
    assert kw["ex"] == 30
    assert val_.endswith("Z")   # ISO-8601 UTC


@pytest.mark.asyncio
async def test_heartbeat_formats_engine_name_from_canonical_constants(redis_mock):
    """Writing with the wrong orthography would break the monitoring endpoint."""
    for canonical in (
        keys.ENGINE_MARKET_DATA, keys.ENGINE_VOL, keys.ENGINE_RISK,
    ):
        redis_mock.set.reset_mock()
        await set_heartbeat(redis_mock, canonical)
        call_args = redis_mock.set.call_args_list
        assert call_args == [call(f"heartbeat:{canonical}", call_args[0][0][1], ex=30)]
